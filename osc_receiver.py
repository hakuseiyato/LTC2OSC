"""
OSC 受信モジュール。中継モードで使用する。
冗長送信による重複は自動で除去する。
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

from pythonosc import dispatcher, osc_server

from tc_generator import Timecode


class _ReusableOSCUDPServer(osc_server.ThreadingOSCUDPServer):
    """SO_REUSEADDR を有効にした UDP OSC サーバ。"""
    allow_reuse_address = True


def is_port_available(port: int) -> bool:
    """UDP ポートが現在空いているか確認する。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class OscReceiver:
    """UDP で OSC を受信し、TC を解析してコールバックする。"""

    def __init__(self, on_tc: Callable[[Timecode], None]):
        self._on_tc = on_tc
        self._server: Optional[_ReusableOSCUDPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._address: str = "/Sync"
        self._port: int = 7000
        self._last_tc_str: str = ""
        self._packet_count: int = 0
        self._dedup_count: int = 0
        self._lock = threading.Lock()

    def configure(self, address: str, port: int) -> None:
        self._address = address
        self._port = port

    def start(self) -> None:
        if self._server is not None:
            return
        disp = dispatcher.Dispatcher()
        disp.map(self._address, self._handler)
        disp.set_default_handler(lambda *args: None)

        self._server = _ReusableOSCUDPServer(("0.0.0.0", self._port), disp)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        # カウンタをリセット
        with self._lock:
            self._packet_count = 0
            self._dedup_count = 0
            self._last_tc_str = ""

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "packets": self._packet_count,
                "dedup": self._dedup_count,
                "last_tc": self._last_tc_str,
            }

    # ── ハンドラ ───────────────────────────────────────────────────────────

    def _handler(self, address: str, *args) -> None:
        if not args:
            return
        tc_str = str(args[0])

        with self._lock:
            self._packet_count += 1
            if tc_str == self._last_tc_str:
                self._dedup_count += 1
                return
            self._last_tc_str = tc_str

        tc = Timecode.parse(tc_str)
        if tc is None:
            return
        try:
            self._on_tc(tc)
        except Exception:
            pass

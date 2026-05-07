"""
中継モード用の PLL 同期クロック。
OSC 受信で位相補正しつつ、内部クロックで連続的に TC を刻む。
UDP パケロスしても音声途切れない。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from tc_generator import FPS_VALUES, Timecode


class RelayClock:
    """OSC で同期される内部 TC クロック。TcGenerator と API 互換。"""

    # 再同期閾値（フレーム単位）
    HARD_SYNC_FRAMES = 10.0       # > これはハード同期（即ジャンプ）
    SOFT_SYNC_FRAMES = 1.0        # > これはソフト同期（部分補正）
    SOFT_SYNC_FACTOR = 0.5        # ソフト同期時の補正率
    MICRO_SYNC_FACTOR = 0.1       # 微小ドリフトの補正率

    def __init__(self, on_tick: Callable[[Timecode], None]):
        self._on_tick = on_tick
        self._fps_label: str = "30NDF"
        self._fps: float = 30.0

        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._lock = threading.Lock()

        self._start_total_frames: int = 0
        self._play_origin: float = 0.0  # perf_counter 基準点
        self._synced: bool = False

        # ステータス
        self._drift_frames: float = 0.0
        self._sync_events: int = 0
        self._last_sync_time: float = 0.0

    def set_fps(self, fps_label: str) -> None:
        self._fps_label = fps_label
        self._fps = FPS_VALUES.get(fps_label, 30.0)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._synced = False
        self._drift_frames = 0.0
        self._sync_events = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    # ── 外部同期 (OSC スレッドから呼ばれる) ─────────────────────────────────

    def on_remote_tc(self, tc: Timecode) -> None:
        now = time.perf_counter()
        remote_frames = tc.to_total_frames(self._fps_label)

        with self._lock:
            if not self._synced:
                # 初回: ハード同期
                self._start_total_frames = remote_frames
                self._play_origin = now
                self._synced = True
                self._drift_frames = 0.0
                self._sync_events += 1
                self._last_sync_time = now
                return

            # 現在の自クロック TC を計算
            local_frames_now = (
                self._start_total_frames + (now - self._play_origin) * self._fps
            )
            drift = remote_frames - local_frames_now  # 正: リモートが先行
            self._drift_frames = drift
            abs_drift = abs(drift)

            if abs_drift > self.HARD_SYNC_FRAMES:
                # 大きくずれた → 即ジャンプ
                self._start_total_frames = remote_frames
                self._play_origin = now
            elif abs_drift > self.SOFT_SYNC_FRAMES:
                # 中程度 → 位相を部分補正
                self._play_origin -= (drift / self._fps) * self.SOFT_SYNC_FACTOR
            else:
                # 微小 → 緩やかに補正
                self._play_origin -= (drift / self._fps) * self.MICRO_SYNC_FACTOR

            self._sync_events += 1
            self._last_sync_time = now

    def get_status(self) -> dict:
        with self._lock:
            since = time.perf_counter() - self._last_sync_time if self._synced else 0.0
            return {
                "synced": self._synced,
                "drift_frames": self._drift_frames,
                "sync_events": self._sync_events,
                "seconds_since_last_sync": since,
            }

    # ── 内部ループ ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        last_frame = -1
        while self._running:
            if not self._synced:
                time.sleep(0.01)
                continue

            with self._lock:
                elapsed = time.perf_counter() - self._play_origin
                total_frames = self._start_total_frames + int(elapsed * self._fps)
                frame_end_time = (
                    self._play_origin
                    + (total_frames - self._start_total_frames + 1) / self._fps
                )

            if total_frames != last_frame:
                last_frame = total_frames
                tc = Timecode.from_total_frames(total_frames, self._fps_label)
                try:
                    self._on_tick(tc)
                except Exception:
                    pass

            sleep_time = frame_end_time - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time * 0.9)

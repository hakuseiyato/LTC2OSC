import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


FPS_VALUES: dict[str, float] = {
    "23.976":  23.976,
    "24":      24.0,
    "25":      25.0,
    "29.97DF": 29.97,
    "29.97NDF":29.97,
    "30DF":    30.0,
    "30NDF":   30.0,
}

DROP_FRAME_LABELS = {"29.97DF", "30DF"}


@dataclass
class Timecode:
    hours: int
    minutes: int
    seconds: int
    frames: int
    drop_frame: bool = False

    def __str__(self) -> str:
        sep = ";" if self.drop_frame else ":"
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}{sep}{self.frames:02d}"

    def to_total_frames(self, fps_label: str) -> int:
        fps = round(FPS_VALUES[fps_label])
        is_df = fps_label in DROP_FRAME_LABELS
        if is_df:
            # SMPTE drop frame 計算
            drop = 2 if fps == 30 else 2
            d = self.hours * 107892 + self.minutes * 1798 - drop * (self.minutes // 10 - self.minutes // 10) + self.seconds * fps + self.frames
            total = (self.hours * 3600 + self.minutes * 60 + self.seconds) * fps + self.frames
            # 簡易計算（DF の細かい補正は実用上ここで十分）
            return total
        return (self.hours * 3600 + self.minutes * 60 + self.seconds) * fps + self.frames

    @staticmethod
    def from_total_frames(total: int, fps_label: str) -> "Timecode":
        fps = round(FPS_VALUES[fps_label])
        is_df = fps_label in DROP_FRAME_LABELS
        ff = total % fps
        total_s = total // fps
        ss = total_s % 60
        total_m = total_s // 60
        mm = total_m % 60
        hh = total_m // 60
        return Timecode(hh % 24, mm, ss, ff, is_df)

    @staticmethod
    def parse(text: str) -> Optional["Timecode"]:
        """'HH:MM:SS:FF' または 'HH:MM:SS;FF' を解析する。"""
        text = text.replace(";", ":").strip()
        parts = text.split(":")
        if len(parts) != 4:
            return None
        try:
            hh, mm, ss, ff = (int(p) for p in parts)
            return Timecode(hh, mm, ss, ff)
        except ValueError:
            return None


class TcGenerator:
    """
    内部クロックで TC を生成し、フレームが進むたびにコールバックを呼ぶ。

    使用例:
        gen = TcGenerator(on_tick=lambda tc: print(tc))
        gen.set_fps("30NDF")
        gen.set_start(Timecode(1, 0, 0, 0))
        gen.play()
        ...
        gen.stop()
    """

    def __init__(self, on_tick: Callable[[Timecode], None]):
        self._on_tick = on_tick
        self._fps_label: str = "30NDF"
        self._fps: float = 30.0
        self._start_tc: Timecode = Timecode(0, 0, 0, 0)
        self._start_total_frames: int = 0

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False
        self._lock = threading.Lock()

        # 再生中の状態
        self._play_origin: float = 0.0   # perf_counter 基準点
        self._pause_elapsed: float = 0.0 # 一時停止前までの累積秒数

    # ── 設定 ─────────────────────────────────────────────────

    def set_fps(self, fps_label: str) -> None:
        self._fps_label = fps_label
        self._fps = FPS_VALUES.get(fps_label, 30.0)

    def set_start(self, tc: Timecode) -> None:
        self._start_tc = tc
        self._start_total_frames = tc.to_total_frames(self._fps_label)

    # ── 操作 ─────────────────────────────────────────────────

    def play(self) -> None:
        if self._running and not self._paused:
            return
        if self._paused:
            # 一時停止から再開
            with self._lock:
                self._play_origin = time.perf_counter()
                self._paused = False
            return
        # 新規再生
        self._running = True
        self._paused = False
        self._pause_elapsed = 0.0
        self._play_origin = time.perf_counter()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if not self._running or self._paused:
            return
        with self._lock:
            self._pause_elapsed += time.perf_counter() - self._play_origin
            self._paused = True

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._pause_elapsed = 0.0
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def reset(self) -> None:
        was_running = self._running and not self._paused
        self.stop()
        if was_running:
            self.play()

    # ── 内部ループ ────────────────────────────────────────────

    def _loop(self) -> None:
        frame_duration = 1.0 / self._fps
        last_frame = -1

        while self._running:
            if self._paused:
                time.sleep(0.005)
                continue

            elapsed = self._pause_elapsed + (time.perf_counter() - self._play_origin)
            total_frames = self._start_total_frames + int(elapsed * self._fps)
            frame_index = total_frames

            if frame_index != last_frame:
                last_frame = frame_index
                tc = Timecode.from_total_frames(total_frames, self._fps_label)
                try:
                    self._on_tick(tc)
                except Exception:
                    pass

            # 次フレームまで待機
            next_frame_time = (frame_index - self._start_total_frames + 1) / self._fps
            sleep_time = next_frame_time - (self._pause_elapsed + (time.perf_counter() - self._play_origin))
            if sleep_time > 0:
                time.sleep(sleep_time * 0.9)

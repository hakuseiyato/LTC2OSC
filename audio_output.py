"""
LTC 音声出力モジュール。
sounddevice OutputStream を使ってリアルタイムに LTC 信号を再生する。
"""
from __future__ import annotations

import threading
from queue import Empty, Queue
from typing import Optional

import numpy as np
import sounddevice as sd


class LtcAudioOutput:
    """
    TC コールバックから push_frame() で 1 フレームずつ音声を受け取り、
    選択した出力デバイスへストリーミングする。

    チャンネル構成は常にステレオ出力。channel_mode で L/R/両方 を切り替える。
    """

    SAMPLE_RATE = 48000
    BLOCKSIZE = 512
    QUEUE_MAXSIZE = 16  # フレーム数

    # channel_mode 定数
    CH_LEFT = 0
    CH_RIGHT = 1
    CH_BOTH = 2

    def __init__(self) -> None:
        self._stream: Optional[sd.OutputStream] = None
        self._queue: Queue[np.ndarray] = Queue(maxsize=self.QUEUE_MAXSIZE)
        self._remainder: np.ndarray = np.array([], dtype=np.float32)
        self._lock = threading.Lock()

        self._device_index: Optional[int] = None
        self._amplitude: float = 0.9
        self._channel_mode: int = self.CH_LEFT
        self._enabled: bool = False

    # ── デバイス列挙 ──────────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        """ストリームが動作中かどうか。"""
        return self._enabled and self._stream is not None

    @staticmethod
    def list_output_devices() -> list[dict]:
        """出力チャンネルを持つデバイスの一覧を返す。"""
        result = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_output_channels"] > 0:
                result.append({"index": i, "name": dev["name"]})
        return result

    # ── 設定 ──────────────────────────────────────────────────────────────────

    def configure(
        self,
        device_index: Optional[int],
        amplitude: float,
        channel_mode: int,
    ) -> None:
        self._device_index = device_index
        self._amplitude = max(0.0, min(1.0, amplitude))
        self._channel_mode = channel_mode

    # ── 制御 ──────────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._stream is not None:
            return
        self._enabled = True
        self._remainder = np.array([], dtype=np.float32)
        self._stream = sd.OutputStream(
            samplerate=self.SAMPLE_RATE,
            blocksize=self.BLOCKSIZE,
            device=self._device_index,
            channels=2,
            dtype="float32",
            callback=self._audio_callback,
            finished_callback=self._on_stream_finished,
        )
        self._stream.start()

    def stop(self) -> None:
        self._enabled = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._flush_queue()

    def push_frame(self, mono: np.ndarray) -> None:
        """TC コールバックスレッドから呼ぶ。溢れたフレームは捨てる。"""
        if not self._enabled:
            return
        # amplitude を適用して積む
        scaled = mono * self._amplitude
        try:
            self._queue.put_nowait(scaled)
        except Exception:
            pass

    # ── 内部 ─────────────────────────────────────────────────────────────────

    def _flush_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def _on_stream_finished(self) -> None:
        pass

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
    ) -> None:
        mono = np.zeros(frames, dtype=np.float32)
        pos = 0

        while pos < frames:
            if len(self._remainder) == 0:
                try:
                    self._remainder = self._queue.get_nowait()
                except Empty:
                    break
            take = min(frames - pos, len(self._remainder))
            mono[pos : pos + take] = self._remainder[:take]
            self._remainder = self._remainder[take:]
            pos += take

        # ステレオへ展開
        outdata[:] = 0.0
        if self._channel_mode == self.CH_LEFT:
            outdata[:, 0] = mono
        elif self._channel_mode == self.CH_RIGHT:
            outdata[:, 1] = mono
        else:
            outdata[:, 0] = mono
            outdata[:, 1] = mono

"""
LTC (SMPTE 12M Linear Timecode) エンコーダ。
外部ライブラリ不要で NumPy だけで BMC 音声信号を生成する。
"""
from __future__ import annotations

import numpy as np

from tc_generator import Timecode, FPS_VALUES

SAMPLE_RATE = 48000

# LTC sync word (bits 64–79): 0011 1111 1111 1101
_SYNC_WORD = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]


def _lsb_bits(value: int, n: int) -> list[int]:
    """value を n ビット LSB ファーストのリストに変換。"""
    return [(value >> i) & 1 for i in range(n)]


def build_ltc_word(tc: Timecode) -> list[int]:
    """80 ビットの LTC ワードを返す（ビット 0 が最初に送信される）。"""
    bits: list[int] = [0] * 80

    fu, ft = tc.frames % 10, tc.frames // 10
    su, st = tc.seconds % 10, tc.seconds // 10
    mu, mt = tc.minutes % 10, tc.minutes // 10
    hu, ht = tc.hours % 10, tc.hours // 10

    # Frame
    for i, b in enumerate(_lsb_bits(fu, 4)):
        bits[0 + i] = b
    # bits[4..7]: User Bits 1 = 0
    for i, b in enumerate(_lsb_bits(ft, 2)):
        bits[8 + i] = b
    # bit[10]: Color Frame = 0
    bits[11] = 1 if tc.drop_frame else 0
    # bits[12..15]: User Bits 2 = 0

    # Seconds
    for i, b in enumerate(_lsb_bits(su, 4)):
        bits[16 + i] = b
    # bits[20..23]: User Bits 3 = 0
    for i, b in enumerate(_lsb_bits(st, 3)):
        bits[24 + i] = b
    # bit[27]: BMPC — 後で設定

    # Minutes
    for i, b in enumerate(_lsb_bits(mu, 4)):
        bits[32 + i] = b
    # bits[36..39]: User Bits 5 = 0
    for i, b in enumerate(_lsb_bits(mt, 3)):
        bits[40 + i] = b
    # bit[43]: BGF0 = 0

    # Hours
    for i, b in enumerate(_lsb_bits(hu, 4)):
        bits[48 + i] = b
    # bits[52..55]: User Bits 7 = 0
    for i, b in enumerate(_lsb_bits(ht, 2)):
        bits[56 + i] = b
    # bit[58]: BGF2 = 0, bit[59]: BGF1 = 0

    # Sync word
    for i, b in enumerate(_SYNC_WORD):
        bits[64 + i] = b

    # BMPC (bit 27): data bits の 1 の総数を偶数にするパリティ
    count = sum(bits[i] for i in range(80) if i != 27)
    bits[27] = count & 1  # 奇数なら 1 にして偶数にする

    return bits


def encode_frame(
    tc: Timecode,
    fps_label: str,
    amplitude: float = 0.9,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """
    1 フレーム分の LTC 音声サンプル（float32, モノラル）を生成する。

    値域: -amplitude 〜 +amplitude
    """
    fps = FPS_VALUES[fps_label]
    bits = build_ltc_word(tc)
    total_samples = int(round(sample_rate / fps))
    audio = np.empty(total_samples, dtype=np.float32)

    polarity: float = 1.0
    for bit_idx, bit in enumerate(bits):
        start = int(round(bit_idx * total_samples / 80))
        end = int(round((bit_idx + 1) * total_samples / 80))
        if start >= end:
            continue
        mid = (start + end) // 2

        # ビット境界でトグル（BMC の規則）
        polarity = -polarity
        audio[start:mid] = polarity * amplitude

        if bit == 1:
            polarity = -polarity

        audio[mid:end] = polarity * amplitude

    return audio

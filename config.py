import json
import os

_DEFAULT = {
    "mode": "sender",  # "sender" or "relay"
    "device_name": "",
    "frame_rate": "30NDF",
    "start_tc": "00:00:00:00",
    "osc_address": "/Sync",
    "targets": [],
    "osc_redundancy": 2,
    "relay_listen_port": 7000,
    # Audio 出力設定
    "audio_enabled": False,
    "audio_device_index": None,
    "audio_amplitude": 0.9,
    "audio_channel_mode": 0,  # 0=Left, 1=Right, 2=Both
}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load() -> dict:
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULT, **data}
        except Exception:
            pass
    return dict(_DEFAULT)


def save(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

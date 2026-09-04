from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PARTS_ROOT = (
    r"C:\Users\thor\Tiny Air Limited\Christopher Helson - Tiny Air OneDrive365"
    r"\Technical File\v2 Engineering\DRAWINGS\DRAWINGS - E\PARTS"
)

APP_DIR = Path.home() / "AppData" / "Roaming" / "TinyAir" / "AccuImporter"
CONFIG_PATH = APP_DIR / "config.json"
COOKIE_PATH = APP_DIR / "accu_cookies.json"


def default_config() -> dict:
    return {
        "parts_root": DEFAULT_PARTS_ROOT,
        "part_prefix": "TA",
        "part_digits": 6,
        "accu_base_url": "https://www.accu.co.uk",
        "accu_email": "",
        "accu_password": "",
        "keep_step": False,
        "inventor_visible": True,
        "open_folder_after_import": True,
        "close_document_after_save": True,
    }


def load_config() -> dict:
    cfg = default_config()
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if k in cfg})
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = default_config()
    merged.update(cfg)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")

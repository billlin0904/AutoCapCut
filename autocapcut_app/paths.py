from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = APP_ROOT / "video-autopilot-kit"
VENDOR_SRC = VENDOR_ROOT / "src"


def ensure_vendor_on_path() -> None:
    """Make the vendored video-autopilot-kit modules importable."""
    src = str(VENDOR_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def require_vendor() -> None:
    if not VENDOR_SRC.exists():
        raise FileNotFoundError(
            f"video-autopilot-kit source not found at {VENDOR_SRC}. "
            "Place the repo under AutoCapCut/video-autopilot-kit."
        )

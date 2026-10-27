"""The packaged ripgrep binary, which the file tools' search runs inside the file boundary."""

import sys
from pathlib import Path


def ripgrep_binary() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "rg"
    return Path(sys.executable).parent / "rg"

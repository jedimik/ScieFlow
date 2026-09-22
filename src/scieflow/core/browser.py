"""Open a URL in the user's browser, including from WSL.

WSL has no usable default browser: `webbrowser.open` silently does nothing
there, so try the Windows helpers first. Never raise — failing to open a
browser must not take down the command that printed the URL.
"""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from pathlib import Path

WSL_OPENERS = ("wslview", "explorer.exe")


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_url(url: str) -> bool:
    """True when something was launched. Best effort by design."""
    if is_wsl():
        for opener in WSL_OPENERS:
            path = shutil.which(opener)
            if path:
                try:
                    subprocess.Popen([path, url], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    continue
        return False
    try:
        return webbrowser.open(url)
    except Exception:       # a broken BROWSER env must not crash `serve`
        return False

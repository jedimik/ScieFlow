from __future__ import annotations

import shutil
import subprocess
import webbrowser
from pathlib import Path

from nicegui import app, ui

from . import interests, reports, run_page
from .context import GuiContext


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_browser(url: str) -> None:
    """Best-effort browser launch; never raises. On WSL, opens the Windows browser."""
    try:
        if is_wsl():
            if shutil.which("wslview"):
                subprocess.Popen(
                    ["wslview", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["explorer.exe", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return
        webbrowser.open(url)
    except Exception:
        pass


def apply_theme() -> None:
    ui.colors(
        primary="#6366f1",
        secondary="#8b5cf6",
        positive="#22c55e",
        negative="#ef4444",
        warning="#f59e0b",
    )


_NAV_ITEMS = [
    ("interests", "star_border", "Interests", "/"),
    ("run", "play_arrow", "Run", "/run"),
    ("reports", "article", "Reports", "/reports"),
]


def shell(active: str) -> ui.column:
    """Slim header with nav + theme toggle; returns the page's content column."""
    dark = ui.dark_mode(value=True)
    with ui.header().classes("items-center justify-between gap-4"):
        with ui.row().classes("items-center gap-6"):
            ui.label("ScieFlow News").classes("text-lg font-bold tracking-wide")
            with ui.row().classes("items-center gap-4"):
                for key, icon, label, target in _NAV_ITEMS:
                    is_active = key == active
                    state_classes = (
                        "text-indigo-400 font-semibold" if is_active else "text-white/60"
                    )
                    link_classes = "flex items-center gap-1 no-underline " + state_classes
                    with ui.link(target=target).classes(link_classes):
                        ui.icon(icon).classes(state_classes)
                        ui.label(label)
        ui.button(icon="light_mode", on_click=dark.toggle).props("flat round")
    return ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4")


def init_pages(ctx: GuiContext) -> None:
    @ui.page("/")
    def _interests() -> None:
        apply_theme()
        with shell("interests"):
            interests.build(ctx)

    @ui.page("/run")
    def _run() -> None:
        apply_theme()
        with shell("run"):
            run_page.build(ctx)

    @ui.page("/reports")
    def _reports() -> None:
        apply_theme()
        with shell("reports"):
            reports.build(ctx)


def start_gui(
    config_path: Path, db_path: Path, port: int = 8080, open_browser_on_start: bool = True
) -> None:
    init_pages(GuiContext(config_path=config_path, db_path=db_path))
    if open_browser_on_start:
        app.on_startup(lambda: open_browser(f"http://127.0.0.1:{port}"))
    ui.run(host="127.0.0.1", port=port, reload=False, show=False, title="ScieFlow News")

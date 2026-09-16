from __future__ import annotations

import html
import re as _re

from filelock import Timeout
from nicegui import ui

from ..report import assemble_report
from .context import GuiContext


def escape_markdown_html(text: str) -> str:
    """Escape raw HTML so agent-sourced markdown cannot inject elements.

    Markdown syntax (headers, emphasis, links, fences) survives escaping;
    only literal <, > and & are neutralized. This also affects blockquote
    `>` markers (rendered as `&gt;` instead of forming a blockquote) and
    means any `<`/`>`/`&` characters inside code fences show up as their
    literal HTML entities rather than the raw characters — an accepted
    tradeoff mandated by the no-raw-HTML security constraint.
    """
    return html.escape(text, quote=False)


def result_filename(run: dict, interest: str) -> str:
    """Generate a safe filename for a per-interest report download."""
    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", interest)
    return f"{run['timestamp'][:10]}-{safe}-run{run['id']}.md"


def normalize_edit(text: str) -> str | None:
    """Blank/whitespace-only edits are treated as "no edit" — store None."""
    return text if text.strip() else None


def run_label(run: dict) -> str:
    label = f"#{run['id']} — {run['timestamp'][:10]} ({run['agent']})"
    failures = run.get("failures", [])
    if failures:
        label += f" — {len(failures)} failed"
    return label


def history_rows(runs: list[dict], db) -> list[dict]:
    """One table row per run, newest first (runs come from db.list_runs())."""
    rows = []
    for run in runs:
        results = db.results_for_run(run["id"])
        rows.append(
            {
                "id": run["id"],
                "date": run["timestamp"][:10],
                "agent": run["agent"],
                "interests": len(results),
                "failed": len(run.get("failures", [])),
            }
        )
    return rows


def build(ctx: GuiContext) -> None:
    ui.label("Reports").classes("text-2xl font-bold")
    db = ctx.open_db()
    runs = db.list_runs()
    if not runs:
        with ui.column().classes("w-full items-center text-gray-500 gap-2 py-12"):
            ui.icon("inbox").classes("text-4xl")
            ui.label("No runs yet — start one on the Run page.")
        return

    state = {"run_id": runs[0]["id"], "interest": "(all)"}

    with ui.row().classes("items-center gap-2 w-full"):
        search_input = ui.input(placeholder="Search all reports…").classes("flex-grow")
        search_input.on("keydown.enter", lambda: do_search())
        ui.button("Search", on_click=lambda: do_search()).props("flat")
    search_results_column = ui.column().classes("w-full gap-1")

    history_column = ui.column().classes("w-full gap-1")
    detail_column = ui.column().classes("w-full gap-4")

    def interest_options() -> list[str]:
        names = sorted({r["name"] for r in db.results_for_run(state["run_id"])})
        return ["(all)"] + names

    def select_run(run_id: int) -> None:
        state["run_id"] = run_id
        state["interest"] = "(all)"
        interest_select.set_options(interest_options(), value="(all)")
        refresh()

    def jump(run_id: int, name: str) -> None:
        select_run(run_id)
        interest_select.set_value(name)  # triggers on_interest_change -> refresh
        search_results_column.clear()

    def do_search() -> None:
        search_results_column.clear()
        try:
            hits = db.search_results(search_input.value or "")
            runs_by_id = {hit["run_id"]: db.get_run(hit["run_id"]) for hit in hits}
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        with search_results_column:
            if not hits:
                ui.label("No matches.").classes("text-sm text-gray-500")
                return
            for hit in hits:
                run = runs_by_id[hit["run_id"]]
                label = f"run #{hit['run_id']} — {run['timestamp'][:10]} — {hit['name']}"
                ui.button(
                    label, on_click=lambda h=hit: jump(h["run_id"], h["name"])
                ).props("flat")

    def edit_dialog(result: dict) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
            ui.label(f"Edit {result['name']}").classes("text-lg")
            editor = (
                ui.textarea(value=result.get("edited_markdown") or result["markdown"])
                .classes("w-full")
                .props("rows=20")
            )

            def save() -> None:
                try:
                    db.set_edited_markdown(result["id"], normalize_edit(editor.value))
                except Timeout:
                    ui.notify("database is busy — try again in a moment", type="warning")
                    return
                dialog.close()
                refresh()

            with ui.row():
                ui.button("Save", on_click=save)
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def reset(result: dict) -> None:
        try:
            db.set_edited_markdown(result["id"], None)
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        refresh()

    def download() -> None:
        try:
            run = db.get_run(state["run_id"])
            content = assemble_report(run, db.results_for_run(state["run_id"]))
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        day = run["timestamp"][:10]
        ui.download(content.encode(), f"{day}-news-run{run['id']}.md")

    def download_result(result: dict) -> None:
        try:
            run = db.get_run(state["run_id"])
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        content = (result.get("edited_markdown") or result["markdown"]).encode()
        ui.download(content, result_filename(run, result["name"]))

    def render_history() -> None:
        history_column.clear()
        with history_column:
            ui.label("Run history").classes("text-sm font-semibold text-gray-400")
            columns = [
                {"name": "id", "label": "#", "field": "id", "align": "left", "sortable": True},
                {"name": "date", "label": "Date", "field": "date", "align": "left", "sortable": True},
                {"name": "agent", "label": "Agent", "field": "agent", "align": "left"},
                {"name": "interests", "label": "Reports", "field": "interests", "align": "right"},
                {"name": "failed", "label": "Failed", "field": "failed", "align": "right"},
            ]
            table = ui.table(
                columns=columns, rows=history_rows(runs, db), row_key="id"
            ).classes("w-full rounded-2xl").props("flat bordered")
            table.on("rowClick", lambda e: select_run(e.args[1]["id"]))

    def refresh() -> None:
        detail_column.clear()
        try:
            run = db.get_run(state["run_id"])
            results = db.results_for_run(state["run_id"])
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        if state["interest"] != "(all)":
            results = [r for r in results if r["name"] == state["interest"]]
        with detail_column:
            ui.label(run_label(run)).classes("text-lg font-semibold")
            for r in results:
                with ui.card().classes("w-full rounded-2xl"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(r["name"]).classes("font-bold text-lg")
                        ui.label(
                            f"window {r['window_start']} → {r['window_end']}"
                        ).classes("text-sm text-gray-400")
                        if r.get("edited_markdown"):
                            ui.badge("edited").props("color=warning")
                    ui.markdown(
                        escape_markdown_html(r.get("edited_markdown") or r["markdown"])
                    )
                    with ui.row():
                        ui.button(
                            icon="edit", on_click=lambda r=r: edit_dialog(r)
                        ).props("flat round").tooltip("Edit")
                        if r.get("edited_markdown"):
                            ui.button(
                                icon="restore", on_click=lambda r=r: reset(r)
                            ).props("flat round color=negative").tooltip("Reset to original")
                        ui.button(
                            icon="download", on_click=lambda r=r: download_result(r)
                        ).props("flat round").tooltip("Download this report")
            failures = run.get("failures", [])
            if failures:
                with ui.card().classes(
                    "w-full rounded-2xl border border-negative/40 bg-negative/5"
                ):
                    for f in failures:
                        with ui.expansion(f"⚠ {f['name']} — {f['reason']}").classes("w-full text-red-600"):
                            if f.get("raw_output"):
                                ui.label("Raw agent output:")
                                ui.label(f["raw_output"]).classes(
                                    "whitespace-pre-wrap font-mono text-xs"
                                )
                            else:
                                ui.label("No output captured (the agent CLI itself failed).")

    def on_interest_change(e) -> None:
        state["interest"] = e.value
        refresh()

    render_history()
    with ui.row().classes("items-center gap-4 w-full"):
        interest_select = ui.select(
            interest_options(),
            value="(all)",
            label="Interest",
            on_change=on_interest_change,
        ).classes("w-52")
        ui.element("div").classes("flex-grow")
        ui.button("Download markdown", on_click=download).props(
            "icon=download"
        )

    refresh()

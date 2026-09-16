from __future__ import annotations

from pathlib import Path

from filelock import Timeout
from nicegui import ui

from ..config import (
    EXAMPLE_CONFIG,
    MAX_GROUP_DEPTH,
    Config,
    ConfigError,
    Group,
    Interest,
    all_group_names,
    load_config,
    save_config,
)
from ..templates import DEFAULT_TEMPLATE, TEMPLATES
from .context import GuiContext
from .groups import (
    add_group,
    delete_group,
    group_depth,
    rename_group,
    set_group_interests,
)


def create_example_config(path: Path) -> None:
    path.write_text(EXAMPLE_CONFIG)


def parse_interest_form(
    name: str,
    context: str,
    repo: str,
    urls_text: str,
    keywords_text: str,
    template: str = DEFAULT_TEMPLATE,
    lookback_text: str = "",
) -> Interest:
    name = name.strip()
    if not name:
        raise ValueError("Name is required")
    if template not in TEMPLATES:
        raise ValueError(f"unknown template: {template!r}")
    lookback_text = lookback_text.strip()
    lookback_days: int | None = None
    if lookback_text:
        try:
            lookback_days = int(lookback_text)
        except ValueError:
            raise ValueError("lookback_days must be a positive integer") from None
        if lookback_days < 1:
            raise ValueError("lookback_days must be a positive integer")
    return Interest(
        name=name,
        context=context.strip() or None,
        repo=repo.strip() or None,
        urls=[u.strip() for u in urls_text.splitlines() if u.strip()],
        keywords=[k.strip() for k in keywords_text.split(",") if k.strip()],
        template=template,
        lookback_days=lookback_days,
    )


def upsert_interest(config: Config, new: Interest, replacing: str | None) -> None:
    names = [i.name for i in config.interests]
    if replacing is None:
        if new.name in names:
            raise ValueError(f"interest {new.name!r} already exists")
        config.interests.append(new)
        return
    if replacing not in names:
        raise ValueError(f"interest {replacing!r} no longer exists — reload the page")
    idx = names.index(replacing)
    if new.name != replacing and new.name in names:
        raise ValueError(f"interest {new.name!r} already exists")
    config.interests[idx] = new


def remove_interest(config: Config, name: str) -> None:
    if len(config.interests) <= 1:
        raise ValueError("cannot delete the last interest — the config must keep at least one")
    config.interests = [i for i in config.interests if i.name != name]


def build(ctx: GuiContext) -> None:
    if not ctx.config_path.exists():
        def create_and_reload() -> None:
            create_example_config(ctx.config_path)
            ui.navigate.reload()

        with ui.column().classes("w-full items-center"):
            with ui.card().classes(
                "w-full max-w-xl rounded-2xl border border-gray-700/40 shadow-sm"
                " items-center text-center gap-2 p-6"
            ):
                ui.icon("waving_hand").classes("text-4xl")
                ui.label("Welcome to ScieFlow News").classes("text-2xl font-bold")
                ui.label(
                    f"No config found at {ctx.config_path}. "
                    "This file holds the interests ScieFlow News researches for you."
                ).classes("text-gray-400")
                ui.button(
                    "Create example config",
                    icon="add_circle",
                    on_click=create_and_reload,
                ).props("color=primary")
        return

    ui.label("Interests").classes("text-2xl font-bold")
    container = ui.column().classes("w-full gap-3")

    def edit_dialog(existing: Interest | None) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
            ui.label("Edit interest" if existing else "Add interest").classes("text-lg")
            name_in = ui.input("Name", value=existing.name if existing else "").classes("w-full")
            context_in = ui.textarea(
                "Context (optional — enables Gaps & Blind Spots)",
                value=(existing.context or "") if existing else "",
            ).classes("w-full")
            repo_in = ui.input(
                "GitHub repo owner/name (optional)",
                value=(existing.repo or "") if existing else "",
            ).classes("w-full")
            urls_in = ui.textarea(
                "URLs, one per line (optional)",
                value="\n".join(existing.urls) if existing else "",
            ).classes("w-full")
            keywords_in = ui.input(
                "Keywords, comma-separated (optional)",
                value=", ".join(existing.keywords) if existing else "",
            ).classes("w-full")
            initial_template = existing.template if existing else DEFAULT_TEMPLATE
            template_select = ui.select(
                {k: t.label for k, t in TEMPLATES.items()},
                value=initial_template,
                label="Template",
            ).classes("w-full")
            template_caption = ui.label(TEMPLATES[initial_template].description).classes(
                "text-xs text-gray-500"
            )

            def on_template_change(e) -> None:
                template_caption.set_text(TEMPLATES[e.value].description)

            template_select.on_value_change(on_template_change)
            lookback_in = ui.input(
                "Lookback days (optional — overrides global first-run window)",
                value=(
                    str(existing.lookback_days)
                    if existing and existing.lookback_days
                    else ""
                ),
            ).classes("w-full")

            def save() -> None:
                try:
                    config = load_config(ctx.config_path)
                    new = parse_interest_form(
                        name_in.value,
                        context_in.value,
                        repo_in.value,
                        urls_in.value,
                        keywords_in.value,
                        template=template_select.value,
                        lookback_text=lookback_in.value,
                    )
                    upsert_interest(
                        config, new, replacing=existing.name if existing else None
                    )
                    save_config(config, ctx.config_path)
                except Timeout:
                    ui.notify("database is busy — try again in a moment", type="warning")
                    return
                except (ConfigError, ValueError) as e:
                    ui.notify(str(e), type="negative")
                    return
                dialog.close()
                refresh()

            with ui.row():
                ui.button("Save", on_click=save)
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def do_delete(name: str) -> None:
        try:
            config = load_config(ctx.config_path)
            remove_interest(config, name)
            save_config(config, ctx.config_path)
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        except (ConfigError, ValueError) as e:
            ui.notify(str(e), type="negative")
            return
        refresh()

    def delete(name: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Delete interest {name!r}? Its context and hints will be lost.")
            with ui.row():

                def confirm() -> None:
                    dialog.close()
                    do_delete(name)

                ui.button("Delete", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def group_edit_dialog(existing: Group | None, parent_default: str | None = None) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("Rename group" if existing else "Add group").classes("text-lg")
            name_in = ui.input("Name", value=existing.name if existing else "").classes(
                "w-full"
            )
            parent_select = None
            if existing is None:
                config = load_config(ctx.config_path)
                options = ["(top level)"] + [
                    g
                    for g in all_group_names(config)
                    if group_depth(config, g) < MAX_GROUP_DEPTH
                ]
                initial = parent_default if parent_default in options else "(top level)"
                parent_select = ui.select(options, value=initial, label="Parent").classes(
                    "w-full"
                )

            def save() -> None:
                try:
                    config = load_config(ctx.config_path)
                    if existing is None:
                        parent = parent_select.value
                        add_group(config, name_in.value, None if parent == "(top level)" else parent)
                    else:
                        rename_group(config, existing.name, name_in.value)
                    save_config(config, ctx.config_path)
                except Timeout:
                    ui.notify("database is busy — try again in a moment", type="warning")
                    return
                except (ConfigError, ValueError) as e:
                    ui.notify(str(e), type="negative")
                    return
                dialog.close()
                refresh()

            with ui.row():
                ui.button("Save", on_click=save)
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def members_dialog(group: Group) -> None:
        config = load_config(ctx.config_path)
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label(f"Members of {group.name!r}").classes("text-lg")
            checkboxes = {
                i.name: ui.checkbox(i.name, value=i.name in group.interests)
                for i in config.interests
            }

            def save() -> None:
                try:
                    config = load_config(ctx.config_path)
                    names = [name for name, cb in checkboxes.items() if cb.value]
                    set_group_interests(config, group.name, names)
                    save_config(config, ctx.config_path)
                except Timeout:
                    ui.notify("database is busy — try again in a moment", type="warning")
                    return
                except (ConfigError, ValueError) as e:
                    ui.notify(str(e), type="negative")
                    return
                dialog.close()
                refresh()

            with ui.row():
                ui.button("Save", on_click=save)
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def do_delete_group(name: str) -> None:
        try:
            config = load_config(ctx.config_path)
            delete_group(config, name)
            save_config(config, ctx.config_path)
        except Timeout:
            ui.notify("database is busy — try again in a moment", type="warning")
            return
        except (ConfigError, ValueError) as e:
            ui.notify(str(e), type="negative")
            return
        refresh()

    def delete_group_confirm(name: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(
                f"Delete group {name!r}? Its subgroups will be removed too "
                "(the interests themselves are kept)."
            )
            with ui.row():

                def confirm() -> None:
                    dialog.close()
                    do_delete_group(name)

                ui.button("Delete", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dialog.close)
        dialog.open()

    def refresh() -> None:
        container.clear()
        try:
            config = load_config(ctx.config_path)
        except ConfigError as e:
            with container:
                ui.label(f"Config error: {e}").classes("text-red-600")
            return
        with container:
            for interest in config.interests:
                with ui.card().classes(
                    "w-full rounded-2xl border border-gray-700/40 shadow-sm"
                    " hover:shadow-md transition-shadow"
                ):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label(interest.name).classes("text-lg font-bold")
                        with ui.row().classes("gap-1"):
                            ui.button(
                                icon="edit", on_click=lambda i=interest: edit_dialog(i)
                            ).props("flat round").tooltip("Edit")
                            ui.button(
                                icon="delete", on_click=lambda i=interest: delete(i.name)
                            ).props("flat round color=negative").tooltip("Delete")
                    if interest.template != DEFAULT_TEMPLATE or interest.lookback_days:
                        with ui.row().classes("gap-1"):
                            if interest.template != DEFAULT_TEMPLATE:
                                ui.badge(TEMPLATES[interest.template].label).props(
                                    "color=secondary"
                                )
                            if interest.lookback_days:
                                ui.badge(f"{interest.lookback_days}d")
                    if interest.context:
                        ui.label(interest.context).classes("text-sm text-gray-400")
                    if interest.keywords:
                        with ui.row().classes("gap-1 flex-wrap"):
                            for k in interest.keywords:
                                ui.badge(k).props("outline")
                    if interest.repo:
                        with ui.row().classes("items-center gap-1 text-xs text-gray-500"):
                            ui.icon("code").classes("text-xs")
                            ui.label(interest.repo)
                    for url in interest.urls:
                        with ui.row().classes("items-center gap-1 text-xs text-gray-500"):
                            ui.icon("link").classes("text-xs")
                            ui.label(url)

            def render_group(group: Group, depth: int) -> None:
                with ui.card().classes(
                    "w-full rounded-2xl border border-gray-700/40 shadow-sm"
                    " hover:shadow-md transition-shadow"
                ).style(f"margin-left: {24 * (depth - 1)}px"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label(group.name).classes("font-bold")
                        with ui.row().classes("gap-1"):
                            ui.button(
                                icon="edit", on_click=lambda g=group: members_dialog(g)
                            ).props("flat round").tooltip("Edit members")
                            ui.button(
                                icon="drive_file_rename_outline",
                                on_click=lambda g=group: group_edit_dialog(g),
                            ).props("flat round").tooltip("Rename")
                            if depth < MAX_GROUP_DEPTH:
                                ui.button(
                                    icon="create_new_folder",
                                    on_click=lambda g=group: group_edit_dialog(
                                        None, parent_default=g.name
                                    ),
                                ).props("flat round").tooltip("Add subgroup")
                            ui.button(
                                icon="delete",
                                on_click=lambda g=group: delete_group_confirm(g.name),
                            ).props("flat round color=negative").tooltip("Delete")
                    if group.interests:
                        with ui.row().classes("gap-1 flex-wrap"):
                            for name in group.interests:
                                ui.badge(name).props("outline")
                for sub in group.groups:
                    render_group(sub, depth + 1)

            ui.label("Groups").classes("text-xl mt-6")
            ui.button(
                "Add group", on_click=lambda: group_edit_dialog(None)
            ).props("icon=create_new_folder").classes("my-2")
            for group in config.groups:
                render_group(group, 1)

    ui.button("Add interest", on_click=lambda: edit_dialog(None)).props(
        "icon=add"
    ).classes("my-2")
    refresh()

from __future__ import annotations

from ..config import MAX_GROUP_DEPTH, Config, Group, all_group_names, find_group


def _depth_of(groups: list[Group], name: str, depth: int = 1) -> int | None:
    for group in groups:
        if group.name == name:
            return depth
        found = _depth_of(group.groups, name, depth + 1)
        if found:
            return found
    return None


def group_depth(config: Config, name: str) -> int:
    depth = _depth_of(config.groups, name)
    if depth is None:
        raise ValueError(f"group {name!r} no longer exists — reload the page")
    return depth


def add_group(config: Config, name: str, parent: str | None) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Group name is required")
    if name in all_group_names(config):
        raise ValueError(f"group {name!r} already exists")
    if parent is None:
        config.groups.append(Group(name=name))
        return
    if group_depth(config, parent) + 1 > MAX_GROUP_DEPTH:
        raise ValueError(f"maximum group depth is {MAX_GROUP_DEPTH}")
    find_group(config, parent).groups.append(Group(name=name))


def rename_group(config: Config, old: str, new: str) -> None:
    new = new.strip()
    if not new:
        raise ValueError("Group name is required")
    group = find_group(config, old)
    if group is None:
        raise ValueError(f"group {old!r} no longer exists — reload the page")
    if new != old and new in all_group_names(config):
        raise ValueError(f"group {new!r} already exists")
    group.name = new


def _remove(groups: list[Group], name: str) -> bool:
    for i, group in enumerate(groups):
        if group.name == name:
            del groups[i]
            return True
        if _remove(group.groups, name):
            return True
    return False


def delete_group(config: Config, name: str) -> None:
    if not _remove(config.groups, name):
        raise ValueError(f"group {name!r} no longer exists — reload the page")


def set_group_interests(config: Config, name: str, names: list[str]) -> None:
    group = find_group(config, name)
    if group is None:
        raise ValueError(f"group {name!r} no longer exists — reload the page")
    known = {i.name for i in config.interests}
    unknown = sorted(set(names) - known)
    if unknown:
        raise ValueError(f"unknown interest: {', '.join(unknown)}")
    group.interests = list(names)

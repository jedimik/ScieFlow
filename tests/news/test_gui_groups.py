import pytest

from scieflow.news.config import Config, Group, Interest
from scieflow.news.gui.groups import (
    add_group,
    delete_group,
    group_depth,
    rename_group,
    set_group_interests,
)


def cfg():
    c = Config(interests=[Interest(name="A"), Interest(name="B")])
    c.groups = [Group(name="top", groups=[Group(name="mid", groups=[Group(name="leaf")])])]
    return c


def test_add_group_root_and_nested():
    c = cfg()
    add_group(c, "extra", parent=None)
    assert [g.name for g in c.groups] == ["top", "extra"]
    add_group(c, "child", parent="mid")
    assert [g.name for g in c.groups[0].groups[0].groups] == ["leaf", "child"]


def test_add_group_depth_limit():
    c = cfg()
    with pytest.raises(ValueError, match="depth"):
        add_group(c, "toodeep", parent="leaf")  # leaf is depth 3


def test_add_group_duplicate_and_unknown_parent():
    c = cfg()
    with pytest.raises(ValueError, match="already exists"):
        add_group(c, "top", parent=None)
    with pytest.raises(ValueError, match="no longer exists"):
        add_group(c, "x", parent="ghost")


def test_rename_group():
    c = cfg()
    rename_group(c, "mid", "middle")
    assert c.groups[0].groups[0].name == "middle"
    with pytest.raises(ValueError, match="already exists"):
        rename_group(c, "middle", "top")


def test_delete_group_keeps_interests():
    c = cfg()
    set_group_interests(c, "mid", ["A"])
    delete_group(c, "mid")  # removes mid and leaf
    assert [g.name for g in c.groups[0].groups] == []
    assert [i.name for i in c.interests] == ["A", "B"]


def test_set_group_interests_validates():
    c = cfg()
    set_group_interests(c, "top", ["A", "B"])
    assert c.groups[0].interests == ["A", "B"]
    with pytest.raises(ValueError, match="unknown interest"):
        set_group_interests(c, "top", ["Nope"])


def test_group_depth():
    c = cfg()
    assert group_depth(c, "top") == 1
    assert group_depth(c, "leaf") == 3

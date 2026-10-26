"""M-B t4 review r1, F4 and F5: the egress rules for a destination that holds runs (C1) and for a
sub-folder of an L2 run (the nblm --workspace bypass). Synthetic layout only; no transport.
"""

import pytest
import yaml

from scieflow.core import events
from scieflow.core.data import egress


def _run(root, name, level):
    run = root / "workspace" / name
    (run / "outputs").mkdir(parents=True)
    (run / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": level, "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))
    return run


@pytest.fixture
def root(tmp_path):
    root_dir = tmp_path / "proj"
    (root_dir / "config").mkdir(parents=True)
    _run(root_dir, "l2run", "L2")
    _run(root_dir, "l0run", "L0")
    return root_dir


def _policy(root, text):
    (root / "config" / "data-policy.yml").write_text(text)


def _listed_remote(root, enforcement="warn"):
    _policy(root, yaml.safe_dump({"version": 1, "enforcement": enforcement, "l2_remotes": [
        {"remote": "meta", "reason": "test: approved remote"}]}))


# --- F4: a destination that holds runs -------------------------------------------------------


def test_fetch_into_a_folder_that_holds_runs_warns_in_the_l0_run(root):
    _listed_remote(root)
    egress.fetch_check(root, root / "workspace", "meta")
    warned = events.read(root / "workspace" / "l0run", types=("data.warning",))
    assert [(e["data"]["rule"], e["data"]["path"]) for e in warned] == [("l2-remote-fetch", ".")]
    assert events.read(root / "workspace" / "l2run", types=("data.warning",)) == []


def test_fetch_into_a_folder_that_holds_runs_is_refused_under_refuse(root):
    _listed_remote(root, enforcement="refuse")
    with pytest.raises(egress.EgressRefused, match="l2-remote-fetch"):
        egress.fetch_check(root, root / "workspace", "meta")


# --- F5: a sub-folder of an L2 run -----------------------------------------------------------


def test_a_sub_folder_of_an_l2_run_is_refused_for_notebooklm(root):
    with pytest.raises(egress.EgressRefused, match="l2run"):
        egress.service_check(root, root / "workspace" / "l2run" / "outputs", "notebooklm")


def test_a_folder_that_holds_an_l2_run_is_refused_for_notebooklm(root):
    with pytest.raises(egress.EgressRefused, match="l2run"):
        egress.service_check(root, root / "workspace", "notebooklm")


def test_a_listed_service_reaches_a_sub_folder_of_an_l2_run(root):
    _policy(root, yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "notebooklm", "reason": "test: reviewed service"}]}))
    egress.service_check(root, root / "workspace" / "l2run" / "outputs", "notebooklm")


def test_an_l0_run_and_its_sub_folder_are_not_refused(root):
    egress.service_check(root, root / "workspace" / "l0run" / "outputs", "notebooklm")

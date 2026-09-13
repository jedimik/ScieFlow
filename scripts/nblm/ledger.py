"""State for one run's claim-check work, under workspace/<slug>/nblm/.

Three files, all plain YAML so the user can read them:
  notebook.yml  the run's notebook id and its uploaded sources (DOI <-> source id)
  quota.yml     questions spent, per UTC day and for the run as a whole
  verdicts.yml  the verdict cache — a re-run of the same claim costs nothing
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _dir(workspace: Path) -> Path:
    return workspace / "nblm"


def _read(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    return yaml.safe_load(path.read_text()) or dict(default)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def utc_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


# --- notebook ----------------------------------------------------------

def load_notebook(workspace: Path) -> dict:
    return _read(_dir(workspace) / "notebook.yml",
                 {"notebook_id": None, "title": None, "profile": None, "sources": []})


def save_notebook(workspace: Path, data: dict) -> None:
    _write(_dir(workspace) / "notebook.yml", data)


def set_notebook(workspace: Path, notebook_id: str, title: str, profile: str) -> dict:
    data = load_notebook(workspace)
    data.update({"notebook_id": notebook_id, "title": title, "profile": profile})
    save_notebook(workspace, data)
    return data


def record_source(workspace: Path, doi: str, source_id: str, path: str,
                  sha256: str) -> dict:
    """Idempotent by DOI: re-uploading the same source updates its row."""
    data = load_notebook(workspace)
    rows = [s for s in data.get("sources", []) if s.get("doi") != doi]
    rows.append({"doi": doi, "source_id": source_id, "file": path, "sha256": sha256})
    data["sources"] = sorted(rows, key=lambda s: s["doi"])
    save_notebook(workspace, data)
    return data


def source_for(workspace: Path, doi: str) -> dict | None:
    for row in load_notebook(workspace).get("sources", []):
        if row.get("doi") == doi:
            return row
    return None


# --- quota -------------------------------------------------------------

def load_quota(workspace: Path) -> dict:
    return _read(_dir(workspace) / "quota.yml", {"run_total": 0, "days": {}})


def questions_today(workspace: Path, now: datetime | None = None) -> int:
    return int(load_quota(workspace)["days"].get(utc_day(now), 0))


def questions_this_run(workspace: Path) -> int:
    return int(load_quota(workspace)["run_total"])


def record_question(workspace: Path, count: int = 1,
                    now: datetime | None = None) -> dict:
    data = load_quota(workspace)
    day = utc_day(now)
    data["days"][day] = int(data["days"].get(day, 0)) + count
    data["run_total"] = int(data["run_total"]) + count
    _write(_dir(workspace) / "quota.yml", data)
    return data


# --- verdict cache -----------------------------------------------------

def cache_key(claim_text: str, doi: str) -> str:
    digest = hashlib.sha256(f"{claim_text}|{doi}".encode("utf-8"))
    return digest.hexdigest()


def load_verdicts(workspace: Path) -> dict:
    return _read(_dir(workspace) / "verdicts.yml", {"verdicts": {}})


def get_verdict(workspace: Path, claim_text: str, doi: str) -> dict | None:
    return load_verdicts(workspace)["verdicts"].get(cache_key(claim_text, doi))


def record_verdict(workspace: Path, claim: dict, payload: dict) -> dict:
    data = load_verdicts(workspace)
    key = cache_key(claim["text"], claim["doi"])
    data["verdicts"][key] = {
        "id": claim["id"],
        "text": claim["text"],
        "doi": claim["doi"],
        "file": claim.get("file"),
        "line": claim.get("line"),
        **payload,
    }
    _write(_dir(workspace) / "verdicts.yml", data)
    return data["verdicts"][key]


def all_verdicts(workspace: Path) -> list[dict]:
    return list(load_verdicts(workspace)["verdicts"].values())

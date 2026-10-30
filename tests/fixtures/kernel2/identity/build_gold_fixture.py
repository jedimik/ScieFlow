"""Build the offline identity gold fixture (M-C2 t4, plan section 5.4 and 6 t4).

Run once, by hand, from the worktree root (not collected by pytest):

    PYTHONPATH=src:. /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 \
        tests/fixtures/kernel2/identity/build_gold_fixture.py

Inputs (read only; their sha256 is checked before anything is written):
  * researcher's gold set  <R>/evals/gold/identity.yaml
  * the 557 recorded answers listed in <P>/identity_files.json (the M-C2 plan probe)
  * the probe's baseline verdicts <P>/identity_baseline_verdicts.json
where <R> = /home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher and <P> = the run's
scratch/m-c2-plan-probe directory (both overridable with --researcher / --probe).

Outputs, written next to this script:
  * gold-responses.json.gz  [{source, endpoint, params, status, body}], one per recorded
    request. `mailto` and `email` are removed from params (no address in any fixture); a
    recorded null body becomes status 404 with body null; every body is reduced to the
    fields the verify-bib parsers read (research/lib/records.py). Sorted, gzip mtime 0.
  * identity_baseline_verdicts.json  copied byte for byte (sha256 checked).
  * identity.yaml                     copied byte for byte (sha256 checked).
  * references.bib                    vendored emit_bib over one entry per gold item.

No network call is made. Nothing outside this directory is written.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_RESEARCHER = Path("/home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher")
DEFAULT_PROBE = Path("/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008"
                     "/scratch/m-c2-plan-probe")

SHA = {
    "identity.yaml": "55f0fe7594b5b9631fb27177be69942b7eef2afa4de716e7a5596e0daa98f27b",
    "identity_files.json": "7c0a7bc72567c37945e4fd4c92b95fc021771a6bc11af75235287a222c3d6db0",
    "identity_baseline_verdicts.json": "bb1b1cc99fb4fdfda62905a463dbec1f6758133e3ca626ef56b18377b6cd444c",
}
EXPECTED_FILES = 557
EXPECTED_ITEMS = 150
DROPPED_PARAMS = ("mailto", "email")

# The fields the parsers read (research/lib/records.py). Anything else is dropped.
OPENALEX_WORK = ("id", "doi", "ids", "title", "display_name", "publication_year",
                 "publication_date", "type", "authorships", "primary_location")
CROSSREF_WORK = ("DOI", "type", "title", "author", "issued", "published", "published-print",
                 "published-online", "created", "container-title")
DATACITE_ITEM = ("id", "attributes")
DATACITE_ATTRS = ("doi", "titles", "creators", "publicationYear", "types", "container")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pick(obj, keys):
    return {k: obj[k] for k in keys if isinstance(obj, dict) and k in obj}


def reduce_openalex(work):
    if not isinstance(work, dict):
        return work
    out = _pick(work, OPENALEX_WORK)
    if isinstance(out.get("authorships"), list):
        out["authorships"] = [
            {"author": _pick(a.get("author") or {}, ("display_name",)),
             "raw_author_name": a.get("raw_author_name")}
            for a in out["authorships"] if isinstance(a, dict)
        ]
    if isinstance(out.get("primary_location"), dict):
        loc = out["primary_location"]
        out["primary_location"] = {**_pick(loc, ("landing_page_url", "pdf_url")),
                                   "source": _pick(loc.get("source") or {}, ("display_name",))}
    return out


def reduce_crossref(work):
    if not isinstance(work, dict):
        return work
    out = _pick(work, CROSSREF_WORK)
    if isinstance(out.get("author"), list):
        out["author"] = [_pick(a, ("given", "family", "name", "suffix"))
                         for a in out["author"] if isinstance(a, dict)]
    return out


def reduce_datacite(item):
    if not isinstance(item, dict):
        return item
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    kept = _pick(attrs, DATACITE_ATTRS)
    if isinstance(kept.get("creators"), list):
        kept["creators"] = [_pick(c, ("name", "givenName", "familyName", "nameType"))
                            for c in kept["creators"] if isinstance(c, dict)]
    if isinstance(kept.get("titles"), list):
        kept["titles"] = [_pick(t, ("title", "titleType")) for t in kept["titles"]
                          if isinstance(t, dict)]
    if isinstance(kept.get("types"), dict):
        kept["types"] = _pick(kept["types"], ("resourceTypeGeneral", "resourceType", "citeproc"))
    if isinstance(kept.get("container"), dict):
        kept["container"] = _pick(kept["container"], ("title",))
    return {"id": item.get("id"), "attributes": kept}


def reduce_body(source: str, body):
    """Reduce one recorded body to the fields its parser reads. Keeps the envelope shape."""
    if body is None:
        return None
    if source == "openalex":
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            return {"results": [reduce_openalex(w) for w in body["results"]]}
        return reduce_openalex(body)
    if source == "crossref":
        message = body.get("message") if isinstance(body, dict) else None
        if isinstance(message, dict) and isinstance(message.get("items"), list):
            return {"message": {"items": [reduce_crossref(w) for w in message["items"]]}}
        return {"message": reduce_crossref(message)}
    if source == "datacite":
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list):
            return {"data": [reduce_datacite(i) for i in data]}
        return {"data": reduce_datacite(data)}
    raise ValueError(f"unknown source {source!r}")


def build(researcher: Path, probe: Path, out: Path) -> dict:
    gold_yaml = researcher / "evals" / "gold" / "identity.yaml"
    files_list = probe / "identity_files.json"
    verdicts = probe / "identity_baseline_verdicts.json"
    for path, name in ((gold_yaml, "identity.yaml"), (files_list, "identity_files.json"),
                       (verdicts, "identity_baseline_verdicts.json")):
        actual = sha256(path)
        if actual != SHA[name]:
            raise SystemExit(f"{name}: sha256 {actual} != the pinned {SHA[name]}")

    paths = json.loads(files_list.read_text(encoding="utf-8"))
    if len(paths) != EXPECTED_FILES or len(set(paths)) != EXPECTED_FILES:
        raise SystemExit(f"expected {EXPECTED_FILES} distinct recorded files, got {len(paths)}")

    responses = []
    for raw_path in paths:
        path = Path(raw_path)
        source = path.parent.name
        data = json.loads(path.read_text(encoding="utf-8"))
        params = {k: v for k, v in (data.get("request_params") or {}).items()
                  if k not in DROPPED_PARAMS}
        body = data.get("response_body")
        responses.append({
            "source": source,
            "endpoint": data["endpoint"],
            "params": params,
            "status": 404 if body is None else 200,
            "body": reduce_body(source, body),
        })
    responses.sort(key=lambda r: (r["source"], r["endpoint"], json.dumps(r["params"], sort_keys=True)))

    text = json.dumps(responses, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if re.search(r"[^\s@\"]+@[^\s@\"]+\.[a-z]{2,}", text, re.I):
        raise SystemExit("an e-mail-like string survived in the fixture; refusing to write it")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "gold-responses.json.gz", "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(text.encode("utf-8"))

    shutil.copyfile(verdicts, out / "identity_baseline_verdicts.json")
    shutil.copyfile(gold_yaml, out / "identity.yaml")

    import yaml  # the project's venv carries PyYAML (research extra)

    items = yaml.safe_load(gold_yaml.read_text(encoding="utf-8"))["items"]
    if len(items) != EXPECTED_ITEMS:
        raise SystemExit(f"expected {EXPECTED_ITEMS} gold items, got {len(items)}")

    from scieflow._vendor.researcher_core.bib import BibEntry, emit_bib

    entries = []
    for index, item in enumerate(items, start=1):
        fields = {"title": str(item["title"]), "doi": str(item["doi"]),
                  "year": str(item["year"])}
        if item.get("first_author"):
            fields["author"] = str(item["first_author"])
        entries.append(BibEntry("article", f"gold{index:03d}", fields))
    (out / "references.bib").write_text(emit_bib(entries), encoding="utf-8")
    return {"responses": len(responses), "items": len(items), "out": str(out)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build the offline identity gold fixture (M-C2 t4).")
    ap.add_argument("--researcher", type=Path, default=DEFAULT_RESEARCHER)
    ap.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args(argv)
    print(json.dumps(build(args.researcher, args.probe, args.out), indent=1))


if __name__ == "__main__":
    main()

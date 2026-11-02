"""M-G t7: the RO-Crate 1.1 structure of `scieflow manuscript crate` (M-G 5.14, M-G-D13). Synthetic only."""

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import manuscript, write
from tests.research.manuscript.conftest import ORCID, RUN
from tests.research.manuscript.crate.crate_support import (
    FINISHED, STAGE_SHA, crate_json, crate_path, crate_run, invoke, make_run,
)

EMAIL = "ada.synthetic@example.test"
DOI = "https://doi.org/10.5555/syn.2010.1"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def by_id(doc) -> dict:
    return {entity["@id"]: entity for entity in doc["@graph"]}


def test_descriptor_and_root_entities(synth):
    crate_run(synth)
    doc = crate_json(synth)
    assert doc["@context"] == "https://w3id.org/ro/crate/1.1/context"
    descriptor, root = doc["@graph"][0], doc["@graph"][1]
    assert descriptor == {
        "@id": "ro-crate-metadata.json", "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"},
    }
    assert root["@id"] == "./" and root["@type"] == "Dataset"
    assert root["name"] == "Gait timing in synthetic cohorts"
    assert root["author"] == [{"@id": "#person-0"}]
    assert {"@id": "manuscript/main.tex"} in root["hasPart"]


def test_entities_after_the_root_are_sorted_by_id(synth):
    crate_run(synth)
    ids = [entity["@id"] for entity in crate_json(synth)["@graph"][2:]]
    assert ids == sorted(ids)


def test_every_reference_resolves_inside_the_graph(synth):
    crate_run(synth)
    doc = crate_json(synth)
    ids = {entity["@id"] for entity in doc["@graph"]}
    refs = []

    def walk(node):
        if isinstance(node, dict):
            if set(node) == {"@id"}:
                refs.append(node["@id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc["@graph"])
    assert refs
    # conformsTo names the external RO-Crate 1.1 profile, which is not an entity of the graph (RO-Crate 1.1).
    external = {"https://w3id.org/ro/crate/1.1"}
    assert sorted(set(refs) - ids - external) == []


def test_file_identifiers_are_sha256_of_the_files(synth):
    crate_run(synth)
    graph = by_id(crate_json(synth))
    expected = {
        "manuscript/main.tex": synth.run_dir / "manuscript" / "main.tex",
        "inputs/metrics.csv": synth.run_dir / "inputs" / "metrics.csv",
        "experiments/denoise/runs/sigma-1.5/metrics.json":
            synth.run_dir / "experiments" / "denoise" / "runs" / "sigma-1.5" / "metrics.json",
    }
    for rel, path in expected.items():
        assert graph[rel]["@type"] == "File"
        assert graph[rel]["identifier"] == f"sha256:{sha(path)}"
        assert graph[rel]["name"] == Path(rel).name


def test_claims_dois_runs_stage_and_person_are_present(synth):
    crate_run(synth)
    doc = crate_json(synth)
    graph = by_id(doc)
    for record in ledger.read(synth.run_dir):
        claim = graph[f"#claim-{record['id']}"]
        assert claim["@type"] == "CreativeWork"
        assert claim["text"] == record["text"]
        assert claim["isPartOf"] == {"@id": f"manuscript/{record['file']}"}
    assert graph[DOI] == {"@id": DOI, "@type": "ScholarlyArticle", "identifier": "10.5555/syn.2010.1"}
    run = graph["#run-denoise-sigma-1.5"]
    assert run["@type"] == "CreateAction"
    assert run["endTime"] == FINISHED
    assert run["instrument"] == {"@id": "#stage-denoise"}
    assert {"@id": "experiments/denoise/runs/sigma-1.5/metrics.json"} in run["result"]
    stage = graph["#stage-denoise"]
    assert stage["@type"] == "SoftwareApplication"
    assert stage["identifier"] == f"sha256:{STAGE_SHA}"
    person = graph["#person-0"]
    assert person["@type"] == "Person"
    assert person["name"] == "Ada Synthetic"
    assert person["identifier"] == f"https://orcid.org/{ORCID}"


def test_no_author_e_mail_and_no_absolute_or_parent_paths(synth):
    crate_run(synth)
    doc = crate_json(synth)
    text = json.dumps(doc, ensure_ascii=False)
    assert EMAIL not in text
    assert "@example.test" not in text
    assert str(synth.root) not in text
    for entity in doc["@graph"]:
        at_id = entity["@id"]
        if at_id.startswith(("#", "http")) or at_id == "./":
            continue
        assert not at_id.startswith("/")
        assert ".." not in at_id.split("/")


def test_written_crate_is_byte_identical_on_rerun(synth):
    crate_run(synth)
    first = invoke("crate", RUN)
    assert first.exit_code == 0, first.output
    once = crate_path(synth).read_bytes()
    second = invoke("crate", RUN)
    assert second.exit_code == 0, second.output
    assert crate_path(synth).read_bytes() == once
    assert once.endswith(b"\n")


def test_no_write_writes_nothing_and_json_prints_the_document(synth):
    crate_run(synth)
    result = invoke("crate", RUN, "--json", "--no-write")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["@graph"][0]["@id"] == "ro-crate-metadata.json"
    assert not crate_path(synth).exists()
    assert events.read(synth.run_dir, types=("note.manuscript_crate",)) == []


def test_a_written_crate_emits_exactly_one_note(synth):
    crate_run(synth)
    result = invoke("crate", RUN)
    assert result.exit_code == 0, result.output
    notes = events.read(synth.run_dir, types=("note.manuscript_crate",))
    assert len(notes) == 1
    assert EMAIL not in json.dumps(notes)


def test_the_crate_needs_a_ledger(synth):
    manuscript_only = synth.run_dir / "manuscript" / "main.tex"
    assert manuscript_only.exists()
    result = CliRunner().invoke(main, ["manuscript", "crate", RUN, "--no-write"], catch_exceptions=False)
    assert result.exit_code == 1


def test_a_symbolic_link_on_the_output_path_is_refused(synth):
    crate_run(synth)
    target = synth.root / "elsewhere"
    target.mkdir()
    (synth.run_dir / "report" / "ro-crate").parent.mkdir(parents=True, exist_ok=True)
    (synth.run_dir / "report" / "ro-crate").symlink_to(target, target_is_directory=True)
    result = CliRunner().invoke(main, ["manuscript", "crate", RUN], catch_exceptions=False)
    assert result.exit_code == 1
    assert list(target.iterdir()) == []


TWO_RUNS = (
    "Sigma was 1.5 in the inline run [run:denoise/sigma-1.5].\n"
    "Sigma was 2.5 in the inline run [run:denoise/sigma-2.5]."
)
OTHER_STAGE_SHA = "c" * 64
BAD_ORCID = "0000-0002-1825-0098"  # the valid iD above with its check character changed


def test_two_runs_of_one_stage_keep_their_own_script_hash(synth):
    manuscript(synth, TWO_RUNS)
    make_run(synth.run_dir, rid="sigma-1.5", stage_sha=STAGE_SHA)
    make_run(synth.run_dir, rid="sigma-2.5", stage_sha=OTHER_STAGE_SHA)
    ledger.build(synth.run_dir, main="main.tex")
    graph = by_id(crate_json(synth))
    first = graph["#run-denoise-sigma-1.5"]["instrument"]["@id"]
    second = graph["#run-denoise-sigma-2.5"]["instrument"]["@id"]
    assert first != second
    assert graph[first]["identifier"] == f"sha256:{STAGE_SHA}"
    assert graph[second]["identifier"] == f"sha256:{OTHER_STAGE_SHA}"


def test_an_orcid_that_fails_its_checksum_is_not_written(synth):
    crate_run(synth)
    metadata = synth.ms / "metadata.yml"
    write(metadata, metadata.read_text(encoding="utf-8").replace(ORCID, BAD_ORCID))
    person = by_id(crate_json(synth))["#person-0"]
    assert person["name"] == "Ada Synthetic"
    assert "identifier" not in person


def test_the_note_payload_carries_counts_only(synth):
    crate_run(synth)
    assert invoke("crate", RUN).exit_code == 0
    notes = events.read(synth.run_dir, types=("note.manuscript_crate",))
    assert set(notes[0]["data"]) == {"entities", "files", "claims", "unreleased"}


def test_a_symbolic_link_manuscript_source_is_refused(synth):
    crate_run(synth)
    outside = synth.root / "outside-refs.bib"
    outside.write_text("@article{x, title={y}}\n", encoding="utf-8")
    bib = synth.ms / "references.bib"
    bib.unlink()
    bib.symlink_to(outside)
    result = CliRunner().invoke(main, ["manuscript", "crate", RUN], catch_exceptions=False)
    assert result.exit_code == 1
    assert not crate_path(synth).exists()


def test_a_symbolic_link_evidence_file_is_refused(synth):
    crate_run(synth)
    evidence = synth.run_dir / "inputs" / "metrics.csv"
    outside = synth.root / "outside-metrics.csv"
    outside.write_bytes(evidence.read_bytes())
    evidence.unlink()
    evidence.symlink_to(outside)
    result = CliRunner().invoke(main, ["manuscript", "crate", RUN], catch_exceptions=False)
    assert result.exit_code == 1
    assert not crate_path(synth).exists()


def test_metadata_absent_gives_no_authors_and_the_run_name(synth):
    crate_run(synth)
    (synth.ms / "metadata.yml").unlink()
    root = crate_json(synth)["@graph"][1]
    assert root["author"] == []
    assert root["name"] == RUN

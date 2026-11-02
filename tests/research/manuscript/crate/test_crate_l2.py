"""M-G t7: an L2 run in the RO-Crate (M-G 5.14). An unreleased data file is an id-only entity; nothing else of it
leaves the run. Synthetic only; RAWMARK values are invented markers that must never appear in the crate.
"""

import hashlib
import json

from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import make_inputs, manuscript, released_record, write_data
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.crate.crate_support import (
    CSV, L2_SENTENCES, RAW, crate_json, crate_path, invoke,
)

EMAIL = "ada.synthetic@example.test"


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_l2(synth) -> None:
    """An L2 run: tbl-metrics is released; tbl-raw is only in the data package (unreleased)."""
    manuscript(synth, L2_SENTENCES)
    write_data(synth.run_dir, default_level="L2",
               released=[released_record(synth.run_dir, "tbl-metrics", "metrics.csv", CSV)])
    make_inputs(synth.run_dir, [{"id": "tbl-raw", "file": "raw.csv", "content": RAW}])
    ledger.build(synth.run_dir, main="main.tex")


def test_unreleased_l2_data_is_an_id_only_entity(synth):
    build_l2(synth)
    doc = crate_json(synth)
    graph = {entity["@id"]: entity for entity in doc["@graph"]}
    raw = graph["#data-tbl-raw"]
    assert raw == {"@id": "#data-tbl-raw", "@type": "CreativeWork", "name": "tbl-raw",
                   "conditionsOfAccess": "L2 data, not released"}
    assert "inputs/raw.csv" not in graph
    assert not any("raw.csv" in json.dumps(entity) for entity in doc["@graph"])
    text = json.dumps(doc, ensure_ascii=False)
    assert sha_text(RAW) not in text
    assert "RAWMARK" not in text and "9913.5" not in text


def test_the_claim_that_rests_on_unreleased_data_points_to_the_id_only_entity(synth):
    build_l2(synth)
    graph = {entity["@id"]: entity for entity in crate_json(synth)["@graph"]}
    claims = [entity for entity in graph.values() if entity["@id"].startswith("#claim-")]
    assert any({"@id": "#data-tbl-raw"} in claim.get("isBasedOn", []) for claim in claims)


def test_a_released_file_is_a_hashed_file_entity(synth):
    build_l2(synth)
    graph = {entity["@id"]: entity for entity in crate_json(synth)["@graph"]}
    released = graph["released/tbl-metrics/metrics.csv"]
    assert released["@type"] == "File"
    assert released["identifier"] == f"sha256:{sha_text(CSV)}"


def test_l2_crate_writes_no_author_e_mail(synth):
    build_l2(synth)
    result = invoke("crate", RUN)
    assert result.exit_code == 0, result.output
    assert EMAIL not in crate_path(synth).read_text(encoding="utf-8")

"""Prompt list and wildcard catalogue (ticket 09, T1.7): `config/scite-prompts.yml`."""

import copy
import json
import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from scieflow.core.review import claims, prompts

ROOT = Path(__file__).resolve().parents[3]
SHIPPED = ROOT / "config" / "scite-prompts.yml"
SCHEMA = ROOT / "src" / "scieflow" / "research" / "schemas" / "scite-prompts.schema.json"

DEFAULT_IDS = ["claim-support", "contested-source", "novelty-claim", "missing-literature",
               "claim-strength", "disputes", "stated-open-questions", "bridges",
               "hypothesis-novelty", "hypothesis-counterevidence"]


def base() -> dict:
    return {"version": 1, "prompts": [
        {"id": "support", "stage": "review", "instruction": "Check {{claim}} citing {{claim_dois}}.",
         "queries": [{"source": "crossref", "template": "{{doi}}"},
                     {"source": "scite_mcp", "tool": "search_literature",
                      "template": "{{novelty_claim}}", "limit": 5}]},
        {"id": "topic", "stage": "gap", "instruction": "Topic {{topic}}.",
         "queries": [{"source": "openalex", "template": "{{topic}} open questions"}]},
    ]}


# -- the shipped file ------------------------------------------------------------
def test_shipped_prompt_list_validates_against_the_schema():
    data = yaml.safe_load(SHIPPED.read_text())
    schema = json.loads(SCHEMA.read_text())
    assert not list(Draft202012Validator(schema).iter_errors(data))


def test_shipped_prompt_list_loads_with_the_ten_default_prompts():
    ps = prompts.load()
    assert [p.id for p in ps.prompts] == DEFAULT_IDS
    stages = {p.id: p.stage for p in ps.prompts}
    assert {stages[i] for i in DEFAULT_IDS[:5]} == {"review"}
    assert {stages[i] for i in DEFAULT_IDS[5:8]} == {"gap"}
    assert {stages[i] for i in DEFAULT_IDS[8:]} == {"hypothesis"}
    for p in ps.prompts:
        assert p.instruction and p.queries, p.id
        assert p.enabled


def test_shipped_prompts_through_the_research_validator(capsys):
    from scieflow.research import validate

    validate.main([str(SHIPPED), "--schema", "scite-prompts"])   # exits 1 when invalid
    assert capsys.readouterr().out.strip() == "OK"


def test_shipped_review_prompts_spend_at_most_three_mcp_queries():
    ps = prompts.load()
    mcp = [q for p in ps.select("review") for q in p.queries if q.source == "scite_mcp"]
    assert len(mcp) <= 3


# -- load-time errors ------------------------------------------------------------
@pytest.mark.parametrize("instruction,needle", [
    ("Use {{secret}} now", "{{secret}}"),
    ("Use {{env:HOME}}", "{{env:HOME}}"),
    ("Use {{claim:x}}", "takes no argument"),
    ("Use {{section}}", "needs an argument"),
    ("Use {{file}}", "needs an argument"),
    ("Use {{ claim }}", "unknown wildcard"),
    ("Use {{claim", "unclosed"),
])
def test_unknown_or_malformed_wildcard_is_a_load_error(instruction, needle):
    data = base()
    data["prompts"][0]["instruction"] = instruction
    with pytest.raises(prompts.PromptError) as exc:
        prompts.parse(data)
    assert needle in str(exc.value)


@pytest.mark.parametrize("template", ["{{section:results}}", "{{file:notes.md}}", "{{doi_usage}}",
                                      "{{unknown}}"])
def test_instruction_only_wildcards_are_refused_in_queries(template):
    data = base()
    data["prompts"][0]["queries"][0]["template"] = template
    with pytest.raises(prompts.PromptError) as exc:
        prompts.parse(data)
    assert "query" in str(exc.value)


@pytest.mark.parametrize("arg", ["../../config/scite.yml", "/etc/passwd", "a//b", "./x", "a/../b",
                                 "a\\b", "~/x", "a/./b", ".hidden/x", "x/"])
def test_file_wildcard_must_be_run_relative(arg):
    data = base()
    data["prompts"][0]["instruction"] = "Read {{file:%s}}" % arg
    with pytest.raises(prompts.PromptError):
        prompts.parse(data)


def test_query_with_two_list_wildcards_or_lonely_claim_dois_is_refused():
    data = base()
    data["prompts"][0]["queries"][0]["template"] = "{{claim}} {{doi}}"
    with pytest.raises(prompts.PromptError, match="one list wildcard"):
        prompts.parse(data)
    data = base()
    data["prompts"][0]["queries"][0]["template"] = "{{claim_dois}}"
    with pytest.raises(prompts.PromptError, match="claim_dois"):
        prompts.parse(data)


def test_schema_errors_become_prompt_errors():
    data = base()
    data["prompts"][0]["queries"][0]["source"] = "google"
    with pytest.raises(prompts.PromptError):
        prompts.parse(data)
    data = base()
    data["prompts"].append(copy.deepcopy(data["prompts"][0]))
    with pytest.raises(prompts.PromptError, match="duplicate"):
        prompts.parse(data)


def test_load_reports_unreadable_yaml(tmp_path):
    path = tmp_path / "p.yml"
    path.write_text("prompts: [\n")
    with pytest.raises(prompts.PromptError):
        prompts.load(path)


# -- run override ----------------------------------------------------------------
def test_override_may_disable_and_add_prompts():
    ps = prompts.parse(base())
    out = prompts.apply_override(ps, {"version": 1, "disable": ["topic"], "prompts": [
        {"id": "extra", "stage": "review", "instruction": "Also look at {{section:discussion}}.",
         "queries": []}]})
    assert [p.id for p in out.prompts] == ["support", "topic", "extra"]
    assert [p.id for p in out.select()] == ["support", "extra"]
    assert out.limits == ps.limits


@pytest.mark.parametrize("override,needle", [
    ({"version": 1, "wildcards": {"claim": {"max_items": 200}}}, "wildcard"),
    ({"version": 1, "wildcard_types": ["secret"]}, "wildcard_types"),
    ({"version": 1, "disable": ["nope"]}, "nope"),
    ({"version": 1, "prompts": [{"id": "support", "stage": "review", "instruction": "x"}]},
     "duplicate"),
    ({"version": 1, "prompts": [{"id": "new", "stage": "review", "instruction": "{{secret}}"}]},
     "{{secret}}"),
    ({"version": 1, "prompts": [{"id": "new", "stage": "review", "instruction": "x",
                                 "queries": [{"source": "crossref",
                                              "template": "{{file:a.md}}"}]}]}, "query"),
    ({"version": 2}, "version"),
    ([], "mapping"),
])
def test_override_never_adds_wildcard_types(override, needle):
    with pytest.raises(prompts.PromptError) as exc:
        prompts.apply_override(prompts.parse(base()), override)
    assert needle in str(exc.value)


def test_load_for_run_reads_the_run_override(tmp_path):
    ws = tmp_path / "ws"
    (ws / "review").mkdir(parents=True)
    (ws / "review" / "prompts.yml").write_text(yaml.safe_dump({"version": 1, "disable": ["bridges"]}))
    ps = prompts.load_for_run(ws)
    assert "bridges" not in [p.id for p in ps.select()]
    assert "bridges" in [p.id for p in prompts.load_for_run(tmp_path / "other").select()]


# -- rendering -------------------------------------------------------------------
def claim(n, section, text, dois=("10.5555/a.1",), novelty=False):
    return claims.Claim(id=f"C{n}", section=section, text=text, dois=tuple(dois), keys=(),
                        file="f.tex", line=n, novelty=novelty)


@pytest.fixture
def ctx(tmp_path) -> prompts.Context:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "reviewer.md").write_text("Focus on actigraphy.\n```\nnot a fence end\n")
    items = [claim(1, "introduction", "Sleep disorders are common"),
             claim(2, "discussion", "Bursts are prodromal markers", ("10.5555/b.2",)),
             claim(3, "methods", "Scoring followed the manual"),
             claim(4, "results", "Actigraphy distinguishes RBD", ("10.5555/a.1", "10.5555/c.3")),
             claim(5, "abstract", "This is the first wearable screen", (), novelty=True)]
    return prompts.Context(
        claims=items, dois=["10.5555/a.1", "10.5555/b.2", "10.5555/c.3"],
        doi_usage={"10.5555/a.1": ["Actigraphy distinguishes RBD"]},
        topic="Actigraphy screening for REM sleep behaviour disorder",
        seed_dois=["10.5555/a.1"], latest_cited_year=2022,
        sections={"results": "Actigraphy separated groups."}, run_dir=tmp_path)


def test_max_items_puts_results_and_discussion_first(ctx):
    ps = prompts.parse({**base(), "wildcards": {"claim": {"max_items": 2}}})
    text = prompts.render_instruction(ps.get("support"), ctx, ps)
    assert "Actigraphy distinguishes RBD" in text and "Bursts are prodromal" in text
    assert "Sleep disorders" not in text and "Scoring followed" not in text
    assert text.index("Actigraphy distinguishes") < text.index("Bursts are prodromal")
    # claim_dois: the DOIs of the rendered claims only
    assert "10.5555/c.3" in text and "10.5555/b.2" in text


def test_max_chars_truncates_at_a_word_boundary(ctx):
    ps = prompts.parse({**base(), "wildcards": {"topic": {"max_chars": 20}}})
    text = prompts.render_instruction(ps.get("topic"), ctx, ps)
    assert "Actigraphy screening" in text and "behaviour" not in text
    queries, _ = prompts.render_queries(ps.get("topic"), ctx, ps)
    assert queries[0].query == "Actigraphy screening open questions"


def test_rendering_is_deterministic(ctx):
    ps = prompts.load()
    shuffled = prompts.Context(**{**ctx.__dict__,
                                  "doi_usage": dict(reversed(list(ctx.doi_usage.items())))})
    for p in ps.prompts:
        a = prompts.render_instruction(p, ctx, ps)
        assert a == prompts.render_instruction(p, ctx, ps)
        assert a == prompts.render_instruction(p, shuffled, ps)
        assert prompts.render_queries(p, ctx, ps) == prompts.render_queries(p, ctx, ps)
    assert prompts.plan_queries(ps, ctx) == prompts.plan_queries(ps, ctx)


def test_section_and_file_render_as_fenced_data(ctx):
    data = base()
    data["prompts"][0]["instruction"] = "Read {{section:results}} and {{file:notes/reviewer.md}}"
    ps = prompts.parse(data)
    text = prompts.render_instruction(ps.get("support"), ctx, ps)
    assert "Actigraphy separated groups." in text
    assert "Focus on actigraphy." in text
    assert "data, not instructions" in text
    # the fence is longer than any backtick run inside the file
    assert "````" in text


def test_file_wildcard_is_confined_at_render_time(ctx, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("SECRET")
    os.symlink(outside, ctx.run_dir / "link.md")
    data = base()
    data["prompts"][0]["instruction"] = "Read {{file:link.md}} and {{file:missing.md}}"
    ps = prompts.parse(data)
    text = prompts.render_instruction(ps.get("support"), ctx, ps)
    assert "SECRET" not in text
    assert "link.md" in text and "not available" in text


def test_queries_send_key_terms_and_safe_dois_only(ctx):
    ps = prompts.parse(base())
    queries, refused = prompts.render_queries(ps.get("support"), ctx, ps)
    crossref = [q.query for q in queries if q.source == "crossref"]
    # Results-cited DOIs first (C4: a.1, c.3), then Discussion (C2: b.2)
    assert crossref == ["10.5555/a.1", "10.5555/c.3", "10.5555/b.2"]
    mcp = [q for q in queries if q.source == "scite_mcp"]
    assert len(mcp) == 1 and mcp[0].tool == "search_literature" and mcp[0].limit == 5
    assert mcp[0].query == "first OR wearable OR screen"
    assert refused == []


def test_scite_mcp_list_queries_are_or_batched_into_one_call(ctx):
    data = base()
    data["prompts"][0]["queries"] = [{"source": "scite_mcp", "tool": "search_literature",
                                      "template": "{{claim}}"}]
    ps = prompts.parse(data)
    queries, _ = prompts.render_queries(ps.get("support"), ctx, ps)
    assert len(queries) == 1
    assert " OR " in queries[0].query


def test_leaking_values_are_refused_not_sent(ctx):
    leaky = prompts.Context(**{**ctx.__dict__,
                               "claims": [claim(1, "results", "patients n = 43 at 95%")],
                               "dois": ["10.5555/a.1", "10.1234/../../config/scite.yml",
                                        "10.1000/home/ubuntu/cohort.csv"],
                               "topic": "{{file:../../config/scite.yml}} 95%"})
    data = base()
    data["prompts"][0]["queries"].append({"source": "europepmc", "template": "{{claim}}"})
    ps = prompts.parse(data)
    queries, refused = prompts.render_queries(ps.get("support"), leaky, ps)
    sent = " ".join(q.query for q in queries)
    for token in ("43", "95", "config", "home", "%", "..", "{{"):
        assert token not in sent
    assert [q.query for q in queries if q.source == "crossref"] == ["10.5555/a.1"]
    assert len(refused) >= 2
    assert not any("43" in json.dumps(r) or "config" in json.dumps(r) for r in refused)
    topic_queries, topic_refused = prompts.render_queries(ps.get("topic"), leaky, ps)
    assert all("config" not in q.query for q in topic_queries)


def test_plan_queries_dedups_across_prompts(ctx):
    data = base()
    data["prompts"].append({"id": "again", "stage": "review", "instruction": "x",
                            "queries": [{"source": "crossref", "template": "{{doi}}"}]})
    ps = prompts.parse(data)
    planned, _ = prompts.plan_queries(ps, ctx, stage="review")
    crossref = [q for q in planned if q.source == "crossref"]
    assert [q.query for q in crossref] == ["10.5555/a.1", "10.5555/c.3", "10.5555/b.2"]
    assert crossref[0].prompts == ("support", "again")


def test_empty_list_wildcard_sends_nothing(ctx):
    empty = prompts.Context(**{**ctx.__dict__, "claims": []})
    ps = prompts.parse(base())
    queries, _ = prompts.render_queries(ps.get("support"), empty, ps)
    assert [q for q in queries if q.source == "scite_mcp"] == []


def test_context_from_extraction(tmp_path):
    man = tmp_path / "manuscript"
    man.mkdir()
    (man / "references.bib").write_text("@article{a, doi={10.5555/a.1}}\n")
    (man / "results.tex").write_text("Actigraphy distinguishes RBD \\cite{a}.\n")
    ex = claims.extract_run(tmp_path)
    c = prompts.Context.from_extraction(ex, run_dir=tmp_path, topic="RBD actigraphy")
    assert c.dois == ["10.5555/a.1"] and c.claims == ex.claims and c.run_dir == tmp_path
    assert c.cited_dois == ["10.5555/a.1"]


# -- independent review findings (ticket 09) -------------------------------------
@pytest.mark.parametrize("template", [
    "cohort_a n=43 95% data/secret.csv {{topic}}",
    "patients cohort_a at /home/ubuntu/secret.csv",
    "{{topic}} since 2020",
    "{{topic}} dataset: x",
])
def test_literal_template_text_must_not_look_like_data(template):
    data = base()
    data["prompts"][1]["queries"][0]["template"] = template
    with pytest.raises(prompts.PromptError, match="literal"):
        prompts.parse(data)
    with pytest.raises(prompts.PromptError, match="literal"):
        prompts.apply_override(prompts.parse(base()), {"version": 1, "prompts": [
            {"id": "new", "stage": "gap", "instruction": "x",
             "queries": [{"source": "openalex", "template": template}]}]})


def test_doi_list_puts_results_and_discussion_dois_first(ctx):
    data = base()
    data["wildcards"] = {"doi": {"max_items": 2}}
    ps = prompts.parse(data)
    queries, _ = prompts.render_queries(ps.get("support"), ctx, ps)
    # C4 (results) cites a.1 and c.3; C2 (discussion) cites b.2
    assert [q.query for q in queries if q.source == "crossref"] == ["10.5555/a.1", "10.5555/c.3"]


def test_mcp_doi_and_claim_dois_respect_max_chars(ctx):
    many = [f"10.5555/long.{'x' * 20}.{chr(97 + i)}" for i in range(26)]
    big = prompts.Context(**{**ctx.__dict__, "dois": many,
                             "claims": [claim(1, "results", "Actigraphy distinguishes RBD", many)]})
    data = base()
    data["wildcards"] = {"cited_dois": {"max_chars": 100}, "claim_dois": {"max_chars": 100}}
    data["prompts"][0]["queries"] = [
        {"source": "scite_mcp", "tool": "search_literature", "template": "{{doi}}"},
        {"source": "scite_mcp", "tool": "search_literature", "template": "{{claim}} {{claim_dois}}"}]
    ps = prompts.parse(data)
    queries, _ = prompts.render_queries(ps.get("support"), big, ps)
    assert len(queries) == 2
    assert len(queries[0].query) <= 100
    assert len(queries[1].query) <= 100 + ps.max_chars("claim") + 1

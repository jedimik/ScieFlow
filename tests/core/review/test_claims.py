"""Citing / novelty sentence extraction and the leak-proof sanitiser (ticket 09, T1.6).

Review Focus #3: numbers, statistics, data references, source comments, LaTeX
pointers, paths and path-shaped DOIs never reach a value that may leave the machine.
"""

from pathlib import Path

import pytest

from scieflow.core.review import claims


# -- reuse, not fork -------------------------------------------------------------
def test_parsing_is_the_claim_check_module():
    from nblm import claims as nblm_claims

    assert claims.parser() is nblm_claims
    assert claims.parser().split_sentences is nblm_claims.split_sentences


# -- sanitiser -------------------------------------------------------------------
LEAKS = [
    ("Sensitivity was 95% (CI 90.3–97.6) for RBD screening", ["95", "90.3", "97.6", "%", "CI"]),
    ("Actigraphy separated groups (p<.001) in the cohort", ["p<", ".001", "001", "<"]),
    ("Actigraphy separated groups (p < 0.001) in the cohort", ["0.001", "<"]),
    ("We recruited patients (n = 43) with RBD", ["43", "n ="]),
    ("Movement bursts were frequent [data:cohort_a] in RBD", ["data", "cohort_a", "["]),
    ("Bursts preceded enactment % source: data/cohort.csv", ["source", "cohort.csv", "data/"]),
    ("Bursts preceded enactment \\input{../secret} in RBD", ["input", "secret", "..", "\\"]),
    ("As shown in Table~\\ref{tab:main}, bursts dominate", ["ref", "tab:main", "\\"]),
    ("Values are in /home/ubuntu/data/cohort.csv for RBD", ["/home", "cohort.csv", "/"]),
    ("Values are in ../../config/scite.yml for RBD", ["config", "scite.yml", ".."]),
    ("Values are in C:\\data\\cohort.xlsx for RBD", ["C:", "cohort", "xlsx", "\\"]),
    ("See https://example.org/x?id=3 for RBD details", ["http", "example.org", "="]),
    ("This replicates 10.1234/abcd.5678 in RBD", ["10.1234", "abcd"]),
    ("Mean age was 67.4 years (SD 5.2) with 12 mg doses", ["67.4", "5.2", "12", "mg", "SD"]),
    ("Odds were higher (OR = 2.3, 95 % CI 1.1-4.8) in RBD", ["2.3", "1.1", "4.8", "OR", "CI"]),
    ("Fullwidth digits \uff14\uff13 patients and \uff19\uff15\uff05 accuracy", ["43", "95", "%"]),
    ("Hidden\u200b4\u200b3 patients with RBD", ["4", "3"]),
    ("A template {{file:../../config/scite.yml}} in prose", ["{", "}", "file:", "config"]),
]


@pytest.mark.parametrize("raw,banned", LEAKS)
def test_sanitiser_removes_review_focus_patterns(raw, banned):
    out = claims.sanitise(raw)
    for token in banned:
        assert token not in out, (token, out)
    assert not any(ch.isdigit() for ch in out), out
    assert claims.leaks(out) == []
    assert claims.check_text(out) == out


def test_sanitiser_keeps_the_literature_meaning():
    out = claims.sanitise("Sensitivity was 95% (CI 90.3–97.6) for RBD screening \\cite{smith2019}.")
    assert out.startswith("Sensitivity was")
    assert "RBD screening" in out
    assert "()" not in out and "( )" not in out


def test_identifiers_with_digits_survive():
    out = claims.sanitise("LRRK2 G2019S carriers and IL-6 levels in COVID-19 and H2O")
    for token in ("LRRK2", "G2019S", "IL-6", "COVID-19", "H2O"):
        assert token in out


@pytest.mark.parametrize("value", [
    "95% accuracy", "p<.001", "n = 43", "[data:cohort_a]", "% source: x", "\\input{x}",
    "../secret", "/etc/passwd", "{{file:x}}", "a 3-month follow-up", "0.5 effect",
    "source: notebook", "user@example.org", "$x$", "\uff14\uff13",
])
def test_residual_leak_check_refuses(value):
    assert claims.leaks(value)
    with pytest.raises(claims.LeakError):
        claims.check_text(value)


def test_leak_error_never_echoes_the_value():
    with pytest.raises(claims.LeakError) as exc:
        claims.check_text("cohort n = 43 secret")
    assert "43" not in str(exc.value) and "secret" not in str(exc.value)


def test_sanitise_refuses_when_nothing_is_left():
    with pytest.raises(claims.LeakError):
        claims.sanitise("95% (p<.001)")


# -- DOIs ------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    "10.1234/../../config/scite.yml", "10.1000/home/ubuntu/cohort.csv", "10.1000//etc/passwd",
    "~/data/x", "../10.1000/x", "10.1000/./x", "10.1000/x\\y", "10.1000/.env",
    "10.1000/data/cohort.xlsx", "/10.1000/x", "10.1000/x y", "not a doi", "", None, 42,
    "10.1000/x\n", "10.1000/{{file:x}}",
])
def test_path_shaped_dois_are_refused(value):
    assert claims.safe_doi(value) is None


@pytest.mark.parametrize("value,expected", [
    ("10.1371/journal.pone.0123456", "10.1371/journal.pone.0123456"),
    ("https://doi.org/10.1016/J.SLEEP.2019.01.001", "10.1016/j.sleep.2019.01.001"),
    ("doi:10.1002/(SICI)1097-0258(19980430)17:8<857::AID-SIM777>3.0.CO;2-E",
     "10.1002/(sici)1097-0258(19980430)17:8<857::aid-sim777>3.0.co;2-e"),
    ("10.5555/rbd.2019.001", "10.5555/rbd.2019.001"),
])
def test_real_dois_pass(value, expected):
    assert claims.safe_doi(value) == expected


# -- novelty and key terms -------------------------------------------------------
@pytest.mark.parametrize("text", [
    "This is the first wearable-only screening approach for RBD",
    "We describe a novel actigraphy marker",
    "An unprecedented accuracy was reached",
    "To our knowledge, no study has combined both",
    "To the best of our knowledge this was never tested",
    "We show for the first time that bursts precede enactment",
    "This association has not been previously reported",
])
def test_novelty_detector(text):
    assert claims.is_novelty(text)


@pytest.mark.parametrize("text", [
    "First, we recruited patients from two centres",
    "Actigraphy distinguishes RBD from controls",
    "The first author screened all records",
])
def test_novelty_detector_ignores_plain_sentences(text):
    assert not claims.is_novelty(text)


def test_key_terms_are_stable_and_meaningful():
    text = "Wrist actigraphy distinguishes REM sleep behaviour disorder from healthy controls in the LRRK2 cohort"
    terms = claims.key_terms(text)
    assert terms == claims.key_terms(text)
    assert terms[:3] == ["wrist", "actigraphy", "distinguishes"]
    assert "the" not in terms and "from" not in terms and "in" not in terms
    assert "lrrk2" in terms
    assert len(claims.key_terms(text, limit=3)) == 3


def test_or_query_unions_terms_once_in_first_seen_order():
    q = claims.or_query(["actigraphy predicts conversion", "actigraphy marks prodromal disease"], limit=5)
    assert q == "actigraphy OR predicts OR conversion OR marks OR prodromal"
    assert claims.leaks(q) == []


# -- extraction ------------------------------------------------------------------
BIB = """@article{smith2019, title={A}, doi={10.5555/rbd.2019.001}}
@article{lee2021,
  title = {B},
  doi = {https://doi.org/10.5555/RBD.2021.007},
}
@article{evil, doi={10.5555/../../config/scite.yml}}
@article{nodoi, title={C}}
"""

RESULTS_A = r"""\section{Results}
% source: data/cohort.csv
Actigraphy distinguished RBD from controls with 95\% accuracy (CI 90.3--97.6) \citep{smith2019}.
Bursts of wrist movement preceded enactment in $n = 43$ patients \cite{lee2021, smith2019}.
Sleep stage was scored manually [data:cohort_a] \cite{nodoi}.
We report the first wearable-only screening approach.
\input{../outside}
"""

DISCUSSION_A = r"""\section{Discussion}
Movement bursts are a prodromal marker \cite{lee2021}.
Unknown references were cited \cite{missingkey}.
"""

INTRO_B = r"""\section{Introduction}
Actigraphy distinguished RBD from controls with 95\% accuracy (CI 90.3--97.6) \citep{smith2019}.
REM sleep behaviour disorder precedes synucleinopathy \cite{evil}.
"""


@pytest.fixture
def run(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    man = ws / "manuscript"
    (man / "drafts" / "claude-paper").mkdir(parents=True)
    (man / "drafts" / "codex-paper").mkdir(parents=True)
    (man / "references.bib").write_text(BIB)
    (man / "drafts" / "claude-paper" / "results.tex").write_text(RESULTS_A)
    (man / "drafts" / "claude-paper" / "discussion.tex").write_text(DISCUSSION_A)
    (man / "drafts" / "codex-paper" / "introduction.tex").write_text(INTRO_B)
    # \input{../outside} from drafts/claude-paper/ resolves to drafts/outside.tex
    (man / "drafts" / "outside.tex").write_text(
        "Secret outside sentence about tremor \\cite{smith2019}.\n")
    return ws


def test_extract_run_finds_citing_and_novelty_sentences(run):
    ex = claims.extract_run(run)
    texts = [c.text for c in ex.claims]
    assert all(c.id == f"C{i}" for i, c in enumerate(ex.claims, 1))
    # the same sentence in two drafts is one claim
    assert sum("Actigraphy distinguished RBD" in t for t in texts) == 1
    first = next(c for c in ex.claims if c.text.startswith("Actigraphy distinguished"))
    assert first.dois == ("10.5555/rbd.2019.001",)
    assert first.section == "results"
    bursts = next(c for c in ex.claims if c.text.startswith("Bursts of wrist"))
    assert bursts.dois == ("10.5555/rbd.2021.007", "10.5555/rbd.2019.001")
    novelty = [c for c in ex.claims if c.novelty]
    assert [c.text for c in novelty] == ["We report the first wearable-only screening approach"]
    assert novelty[0].dois == ()
    assert any(c.section == "discussion" for c in ex.claims)
    assert any(c.section == "introduction" for c in ex.claims)


def test_extract_run_never_carries_leaks(run):
    ex = claims.extract_run(run)
    blob = " ".join(c.text for c in ex.claims) + " ".join(
        s for v in ex.doi_usage.values() for s in v)
    for token in ("95", "90.3", "43", "cohort", "source", "data", "%", "Secret", "tremor"):
        assert token not in blob, token
    for c in ex.claims:
        assert claims.leaks(c.text) == []


def test_extract_run_confines_inputs_to_the_manuscript(run):
    ex = claims.extract_run(run)
    assert all("outside" not in c.file for c in ex.claims)
    assert not any("tremor" in c.text for c in ex.claims)


def test_extract_run_reports_unresolved_and_refused_dois(run):
    ex = claims.extract_run(run)
    assert {"key": "missingkey", "reason": "key not in bib"} in ex.unresolved
    assert {"key": "nodoi", "reason": "bib entry has no doi field"} in ex.unresolved
    assert {"key": "evil", "reason": "not a safe DOI"} in ex.invalid_dois
    assert ex.dois == ["10.5555/rbd.2019.001", "10.5555/rbd.2021.007"]
    assert all("config" not in d for d in ex.dois)
    # the sentence citing only a refused DOI is kept locally, with no DOI
    syn = next(c for c in ex.claims if "synucleinopathy" in c.text)
    assert syn.dois == ()


def test_doi_usage_maps_dois_to_sanitised_sentences(run):
    ex = claims.extract_run(run)
    assert sorted(ex.doi_usage) == ["10.5555/rbd.2019.001", "10.5555/rbd.2021.007"]
    assert any("prodromal marker" in s for s in ex.doi_usage["10.5555/rbd.2021.007"])


def test_extraction_is_deterministic(run):
    assert claims.extract_run(run) == claims.extract_run(run)


def test_refused_sentences_are_reported_without_their_text(run, monkeypatch):
    real = claims.check_text

    def strict(value):
        if "prodromal" in value:
            raise claims.LeakError("residual leak")
        return real(value)

    monkeypatch.setattr(claims, "check_text", strict)
    ex = claims.extract_run(run)
    assert not any("prodromal" in c.text for c in ex.claims)
    assert ex.refused and all(set(r) == {"file", "line", "reason"} for r in ex.refused)
    assert not any("prodromal" in str(r) for r in ex.refused)


def test_merged_manuscript_without_drafts(tmp_path):
    man = tmp_path / "manuscript"
    (man / "sections").mkdir(parents=True)
    (man / "references.bib").write_text(BIB)
    (man / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\\input{sections/results}\n\\end{document}\n")
    (man / "sections" / "results.tex").write_text(
        "\\section{Results and Discussion}\nBursts precede enactment \\cite{lee2021}.\n")
    ex = claims.extract_run(tmp_path)
    assert [c.text for c in ex.claims] == ["Bursts precede enactment"]
    assert ex.claims[0].section == "results"
    assert ex.claims[0].file == "sections/results.tex"


def test_no_manuscript_is_empty(tmp_path):
    ex = claims.extract_run(tmp_path)
    assert ex.claims == [] and ex.dois == []


# -- priority --------------------------------------------------------------------
def test_prioritise_puts_results_then_discussion_first_stably():
    items = [claims.Claim(id=f"C{i}", section=s, text=f"t{i}", dois=(), keys=(), file="f",
                          line=i, novelty=False)
             for i, s in enumerate(["introduction", "discussion", "methods", "results",
                                    "results", "abstract", "discussion"], 1)]
    ordered = [c.id for c in claims.prioritise(items)]
    assert ordered == ["C4", "C5", "C2", "C7", "C1", "C3", "C6"]


@pytest.mark.parametrize("heading,section", [
    ("Results", "results"), ("Results and Discussion", "results"),
    ("Discussion", "discussion"), ("Materials and Methods", "methods"),
    ("Introduction", "introduction"), ("Conclusions", "conclusion"),
    ("Clinical Implications", "clinical-implications"),
])
def test_section_names_are_normalised(heading, section):
    assert claims.section_name(heading) == section


# -- independent review findings (ticket 09) -------------------------------------
@pytest.mark.parametrize("raw,banned", [
    ("Participant sub-01 showed the first ever response", ["sub-01", "01"]),
    ("Patient P-12 and P-17 responded to the novel drug", ["P-12", "P-17", "12", "17"]),
    ("subject S-0043 and site A-7 enrolled", ["0043", "A-7"]),
    ("Day-28 mortality and age-65 adults at week-12 visits", ["28", "65", "12"]),
    ("In n43 and e-43 and S-43a and grp-A-12 groups", ["43", "12"]),
    ("Values V-0.5 and p-value-0.03 in v2.3.1", ["0.5", "0.03", "v2", "3.1"]),
    ("Patients RBD043 and PD-012 were screened", ["RBD043", "043", "012"]),
    ("Cohort [dataset:cohort_a] showed a novel effect", ["dataset", "cohort_a", "cohort a"]),
    ("Cohort [dataset: cohort_a] showed a novel effect", ["dataset", "cohort"]),
    ("See table: tab_main and variable: age_onset for RBD", ["table:", "tab", "age_onset", "onset"]),
    ("A novel effect \\input secret_cohort here", ["secret", "input"]),
    ("A novel effect \\include{chapters/secret} here", ["secret", "include"]),
])
def test_review_leaks_are_removed(raw, banned):
    out = claims.sanitise(raw)
    for token in banned:
        assert token not in out, (token, out)
    assert claims.leaks(out) == []


@pytest.mark.parametrize("value", ["sub-01", "P-12", "Day-28", "n43", "S-43a", "RBD043",
                                   "v2.3.1", "dataset: x", "table: y"])
def test_residual_check_flags_glued_ids_and_labels(value):
    assert claims.leaks(value)


@pytest.mark.parametrize("value", ["LRRK2", "G2019S", "A53T", "IL-6", "COVID-19", "SARS-CoV-2",
                                   "H2O", "HbA1c", "CD4", "APOE4", "IL-1β"])
def test_gene_and_protein_identifiers_are_not_leaks(value):
    assert claims.leaks(value) == []
    assert value in claims.sanitise(f"Carriers of {value} were studied")


@pytest.mark.parametrize("value", [
    "10.1234/x%2F..%2F..%2Fadmin", "10.1234/x/y.CSV;", "10.1234/x/secret.tsv?",
    "10.1234/a/b.sas7bdat", "10.1234/a/b.npy", "10.1234/a/b.mat", "10.1234/a/b.pkl",
    "10.1234/a/b.png", "10.1234/a/b.csv.bak",
    "10.1234/ubuntu/scieflow/workspace/run1/data/cohort_a",
])
def test_more_path_shaped_dois_are_refused(value):
    assert claims.safe_doi(value) is None


def test_extraction_never_reads_outside_the_draft(tmp_path, monkeypatch):
    man = tmp_path / "ws" / "manuscript"
    draft = man / "drafts" / "agent-a"
    draft.mkdir(parents=True)
    (man / "references.bib").write_text(BIB)
    (draft / "main.tex").write_text("\\input{../../../../secret}\nBursts precede enactment \\cite{lee2021}.\n")
    (tmp_path / "secret.tex").write_text("\\input{secret2}\nSecret \\cite{lee2021}.\n")
    (tmp_path / "secret2.tex").write_text("Secret two \\cite{lee2021}.\n")
    read: list[Path] = []
    real = Path.read_text

    def spy(self, *a, **k):
        read.append(self)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    ex = claims.extract_run(tmp_path / "ws")
    assert [c.text for c in ex.claims] == ["Bursts precede enactment"]
    assert not any("secret" in p.name for p in read), read


def test_fifo_input_does_not_block(tmp_path):
    import os
    man = tmp_path / "manuscript"
    man.mkdir()
    (man / "references.bib").write_text(BIB)
    os.mkfifo(man / "pipe.tex")
    (man / "main.tex").write_text("\\input{pipe}\nBursts precede enactment \\cite{lee2021}.\n")
    ex = claims.extract_run(tmp_path)
    assert [c.text for c in ex.claims] == ["Bursts precede enactment"]


def test_headings_with_short_titles_and_abstract_end(tmp_path):
    man = tmp_path / "manuscript"
    man.mkdir()
    (man / "references.bib").write_text(BIB)
    (man / "main.tex").write_text(
        "\\begin{abstract}\nA novel screen is proposed.\n\\end{abstract}\n"
        "Bursts precede enactment \\cite{lee2021}.\n"
        "\\section[Res]{Results}\nActigraphy distinguishes RBD \\cite{smith2019}.\n")
    ex = claims.extract_run(tmp_path)
    by_text = {c.text: c.section for c in ex.claims}
    assert by_text == {"A novel screen is proposed": "abstract",
                       "Bursts precede enactment": "body",
                       "Actigraphy distinguishes RBD": "results"}

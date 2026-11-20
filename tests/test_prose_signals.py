"""scripts/prose_signals.py: the signals of inventory prose (src/scieflow/research/templates/article-prose.md).

INVENTORY and ARGUED carry the same facts. INVENTORY reports one state per sentence; ARGUED joins each
fact to what follows from it. The script has to tell the two apart, read running text only, and never
call a text good: its report can only send a reader back to the text.
"""

import json
import subprocess
import sys
from pathlib import Path

import prose_signals as ps

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prose_signals.py"

INVENTORY = r"""\section{Software description}

The tool has three layers.
The user layer holds one configuration file and the command with four subcommands.
The workflow layer is a workflow manager.
The external tools run in containers grouped by tool family.
The record layer writes a record of every run.
It does not hold the release version outside a work tree.
"""

ARGUED = r"""\section{Software description}

% argues: one study file is all a user edits, so a study can be repeated from that file alone
Because every output is resolved from one study file, a run can be specified, checked and repeated from a single document.
The stages exchange only named outputs, so shared preprocessing runs once while alternative branches run side by side.
Since each external tool runs in its own container, a new tool can be added while the environments of the others stay unchanged.

% argues: every result can be traced to what produced it, which turns a failed run into a short search
Each run writes a record of the code, the configuration and the inputs that produced it, so a published result can be traced to its origin.
When a product fails its check, the report names the stage that produced it, which shortens the search for the cause.
The same study file runs on a workstation and on a cluster, so a study can move between them unchanged.
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _report(capsys, *argv):
    code = ps.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


# --- the two versions of one paragraph ------------------------------------------------------


def test_inventory_raises_the_short_sentence_and_the_relation_signal(tmp_path, capsys):
    code, report = _report(capsys, _write(tmp_path, "software.tex", INVENTORY))
    assert code == 0                                           # signals are information by default
    text = report["text"]
    assert (text["sentences"], text["words"]) == (6, 55)
    assert text["words_per_sentence"] == 9.2
    assert text["relation_share"] == 0.0
    assert any(s.startswith("short sentences (9.2 words") for s in report["signals"])
    assert any(s.startswith("few sentences state a relation (0%)") for s in report["signals"])
    runs = [item for item in report["look_again"] if item["kind"] == "inventory run"]
    assert len(runs) == 1
    assert runs[0]["where"].endswith("software.tex:3")         # the first line of the paragraph
    assert runs[0]["why"] == "6 sentences in a row state no relation"
    assert runs[0]["starts"].startswith("The tool has three layers.")


def test_the_argued_version_raises_no_signal(tmp_path, capsys):
    code, report = _report(capsys, _write(tmp_path, "software.tex", ARGUED))
    assert code == 0
    text = report["text"]
    assert (text["paragraphs"], text["sentences"], text["words"]) == (2, 6, 129)
    assert text["words_per_sentence"] == 21.5
    assert text["relation_share"] == 1.0
    assert report["signals"] == []
    assert report["look_again"] == []


def test_a_clean_report_says_that_it_proves_nothing(tmp_path, capsys):
    assert ps.main([_write(tmp_path, "software.tex", ARGUED)]) == 0
    out = capsys.readouterr().out
    assert "Signals: none. This proves nothing: read the text as its reader." in out
    assert "passed" not in out.lower() and "good" not in out.lower()


def test_a_model_text_sets_the_bar(tmp_path, capsys):
    model = _write(tmp_path, "model.tex", ARGUED)
    code, report = _report(capsys, _write(tmp_path, "software.tex", INVENTORY), "--model", model)
    assert code == 0
    assert report["model"]["words_per_sentence"] == 21.5
    joined = " | ".join(report["signals"])
    assert "sentences are shorter than the model text (9.2 against 21.5 words)" in joined
    assert "fewer sentences state a relation than in the model text (0% against 100%)" in joined
    code, report = _report(capsys, model, "--model", model)
    assert report["signals"] == []


# --- what counts as running text -------------------------------------------------------------


def test_floats_listings_headings_and_comments_are_not_read(tmp_path, capsys):
    text = r"""\section{Results}
\label{sec:results}

The run finished in fourteen minutes on four cores. % source: [data:r1]
\begin{table}
\caption{Nine words that must never be counted at all.}
\begin{tabular}{ll}
route & 0.89 \\
\end{tabular}
\end{table}
\begin{lstlisting}
tool run --config study.yaml
\end{lstlisting}
% a note of the writer, which is no text either
"""
    _code, report = _report(capsys, _write(tmp_path, "results.tex", text))
    assert (report["text"]["paragraphs"], report["text"]["sentences"], report["text"]["words"]) == (1, 1, 9)


def test_an_escaped_percent_sign_is_text_and_a_source_comment_is_not(tmp_path, capsys):
    text = "The share rose to 45\\% of sentences. % source: [data:x] 999 words here\n"
    _code, report = _report(capsys, _write(tmp_path, "results.tex", text))
    assert report["text"]["words"] == 7


def test_citations_and_references_add_no_words(tmp_path, capsys):
    text = ("The workflow manager \\cite{koster2012,molder2021} resolves the job graph "
            "(Figure~\\ref{fig:graph}), so each job runs once.\n")
    _code, report = _report(capsys, _write(tmp_path, "software.tex", text))
    # "N" stands for the reference, so "Figure N" stays two words; the cite keys are gone
    assert report["text"]["words"] == 14


def test_abbreviations_do_not_end_a_sentence():
    text = ("Fig. 2 shows the route, e.g. the registered masks, as Smith et al. found in 0.89 of cases. "
            "The second sentence follows.")
    assert len(ps.split_sentences(text)) == 2


def test_plain_text_and_markdown_are_read_without_code_tables_and_headings(tmp_path, capsys):
    text = """# A heading that is no sentence

Because the stages exchange named outputs, shared work runs once.

```
tool run --config study.yaml and more words in code
```

| a | table row |
|---|---|

- The same study file runs on a workstation and on a cluster.
"""
    _code, report = _report(capsys, _write(tmp_path, "notes.md", text))
    assert (report["text"]["paragraphs"], report["text"]["sentences"], report["text"]["words"]) == (2, 2, 22)


# --- identifiers -----------------------------------------------------------------------------


def test_a_sentence_with_three_identifiers_is_listed_as_a_table_row(tmp_path, capsys):
    text = ("The block \\texttt{enabled\\_stages} switches stages, \\texttt{WTA\\_tractography} declares "
            "combinations, and \\texttt{execution\\_scope} bounds a run.\n")
    _code, report = _report(capsys, _write(tmp_path, "software.tex", text))
    rows = [item for item in report["look_again"] if item["kind"] == "table row"]
    assert len(rows) == 1 and rows[0]["why"] == "3 identifiers in one sentence"


# --- the argues line -------------------------------------------------------------------------


def test_an_argues_line_belongs_to_the_paragraph_below_it(tmp_path, capsys):
    path = _write(tmp_path, "software.tex", ARGUED + "\nThe report is mirrored as a file, so other tools can read it.\n")
    _code, report = _report(capsys, path)
    assert report["text"]["paragraphs"] == 3
    assert report["text"]["paragraphs_without_argues"] == 1
    assert not any("argues" in s for s in report["signals"])   # asked for only with --argues
    _code, report = _report(capsys, path, "--argues")
    assert "1 of 3 paragraphs carry no '% argues:' line" in report["signals"]


def test_an_argues_line_that_names_a_topic_is_listed(tmp_path, capsys):
    text = ("% argues: Describes the configuration file and its three blocks\n"
            "Because every output is resolved from one study file, a run can be repeated from it.\n\n"
            "% argues: Provenance\n"
            "Each run writes a record, so a result can be traced to its origin.\n\n"
            "% argues: one study file is all a user edits, so a study can be repeated from it alone\n"
            "The same study file runs on a workstation and on a cluster, so a study can move between them.\n")
    _code, report = _report(capsys, _write(tmp_path, "software.tex", text))
    topics = [item for item in report["look_again"] if item["kind"] == "topic, not a claim"]
    assert [item["where"].rsplit(":", 1)[1] for item in topics] == ["2", "5"]
    assert "Describes the configuration file" in topics[0]["why"]


# --- short texts and rates -------------------------------------------------------------------


def test_a_text_of_fewer_than_five_sentences_is_not_judged(tmp_path, capsys):
    text = "The tool has three layers.\nThe workflow layer is a workflow manager.\n"
    code = ps.main([_write(tmp_path, "abstract.tex", text), "--json", "--strict"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["signals"] == []
    assert report["notes"] == ["2 sentences: fewer than 5, so no rate is judged"]


def test_the_negation_rate_is_judged_from_300_words_on(tmp_path, capsys):
    line = "The record does not hold the release version outside a work tree in this mode.\n"
    _code, short = _report(capsys, _write(tmp_path, "short.tex", line * 6))
    assert not any("negations" in s for s in short["signals"])
    assert "90 words: fewer than 300, so the negation and identifier rates are not judged" in short["notes"]
    _code, long = _report(capsys, _write(tmp_path, "long.tex", line * 25))
    assert long["text"]["words"] == 375
    assert any(s.startswith("many negations (66.7 per 1,000 words)") for s in long["signals"])


# --- the command -----------------------------------------------------------------------------


def _run(*argv):
    # -S and -I: no site-packages and no user environment, so a third-party import would fail here
    return subprocess.run([sys.executable, "-S", "-I", str(SCRIPT), *argv],
                          capture_output=True, text=True, check=False)


def test_the_script_runs_on_the_standard_library_and_prints_its_help():
    done = _run("--help")
    assert done.returncode == 0, done.stderr
    assert "--model" in done.stdout and "--argues" in done.stdout and "--strict" in done.stdout


def test_exit_status_is_0_by_default_1_with_strict_and_2_for_a_missing_file(tmp_path):
    inventory = _write(tmp_path, "software.tex", INVENTORY)
    assert _run(inventory).returncode == 0
    assert _run(inventory, "--strict").returncode == 1
    assert _run(_write(tmp_path, "argued.tex", ARGUED), "--strict").returncode == 0
    missing = _run(str(tmp_path / "nowhere.tex"))
    assert missing.returncode == 2
    assert "no such file" in missing.stderr and "nowhere.tex" in missing.stderr


def test_the_json_report_has_the_documented_keys(tmp_path, capsys):
    _code, report = _report(capsys, _write(tmp_path, "software.tex", INVENTORY))
    assert set(report) == {"text", "model", "signals", "notes", "look_again"}
    assert report["model"] is None
    assert set(report["text"]) == {
        "paragraphs", "sentences", "words", "words_per_sentence", "relation_share",
        "negations_per_1000_words", "identifiers_per_100_words", "sentences_over_45_words",
        "paragraphs_without_argues",
    }
    assert all(set(item) == {"kind", "where", "why", "starts"} for item in report["look_again"])

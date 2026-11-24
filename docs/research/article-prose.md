# Article Prose

How ScieFlow agents are told to write the text of an article, why these instructions exist, and
how you steer them. The instructions are one file, `src/scieflow/research/templates/article-prose.md`.
This page explains them in plain language.

## The problem

An agent that drafts from a list of checked facts tends to write an **inventory**: one true
statement after another.

> The tool has three layers. The user layer holds one configuration file, the command with its four
> subcommands and the web documentation. The workflow layer is a workflow manager. The external
> tools run in containers grouped by tool family.

Every sentence is correct, and the reader still does not learn what the design is for. A scientific
article needs the same facts as an **argument**, which says what they mean:

> Because every output is resolved from one study file, a study can be specified, checked and
> repeated from a single document. The stages exchange only named outputs and each external tool
> runs in its own container, so a new tool can be added while the environments of the others stay
> as they are.

The second version is more abstract and it is not less accurate. It names the idea, and the facts
under it are the evidence.

ScieFlow used to push its writers toward the first version, for three reasons:

1. The draft prompt carried rules about sources and no rule about writing.
2. The reviewer persona hunted unsupported claims. A list of checked facts drew no comment, while a
   general statement drew one, so every review round made the text more literal.
3. No step read the text the way its reader will.

## What an agent is told now

| Step of the [paper draft](workflows/paper-draft.md) | Before | Now |
| --- | --- | --- |
| Intake | nothing about language | asks you once for a model text and stores it in `inputs/style/` |
| Outline | a list of claims per section | the paragraphs of each section, each with an `ARGUES:` line and the facts under it |
| Draft | source rules only | the whole guide is pasted into every draft prompt, and every paragraph gets a `% argues:` line |
| Review | hunts unsupported claims | also reads as the journal's reader and reports paragraphs that only list facts |
| Revision | minimal changes | the sentences it writes follow the guide |
| Merge | picks the clearer version | judges how each version reads by the guide, then runs a prose step before anything is verified |

The reviewer persona and the revision prompt are shared with the paper review and the journal
revision, so the same instructions reach those workflows.

## The guide in short

- **The reader** is a researcher of the field who has never seen the data folder, the code or the
  agent's notes.
- **Two failures.** An inventory reports what exists and never says what it means. Fog is general
  praise with no case under it ("robust, flexible and user-friendly").
- **The ladder.** Every statement is a state, a mechanism, a consequence or a principle. A
  paragraph states its claim as a consequence or a principle and goes down to the mechanism and the
  state for what makes the claim checkable.
- **The paragraph** makes one argument: a claim, what carries it, and what follows from it.
- **The sentence** joins a fact to its reason, purpose, condition, contrast or consequence.
- **What goes where.** File names, configuration keys and long lists go into tables and listings.
  Limits are collected in one limitations paragraph, apart from a limit that changes how one result
  must be read.
- **Accuracy stays.** An abstraction is a claim and is checked like one.

## The `% argues:` line

Above every paragraph an agent writes, it puts one comment line that states the claim of the
paragraph:

```latex
% argues: one study file is all a user edits, so a study can be repeated from that file alone
Because every output is resolved from one study file, a study can be specified, checked and
repeated from a single document.
```

The line is a LaTeX comment, so it never reaches the PDF. It is not counted as words, and the
[claim ledger](claims.md) reads the manuscript the same with and without it. Reviewers receive the
draft without comments, so they judge the text as a reader does.

The line is useful to you as well. The `ARGUES:` lines of the outline show the argument of the
whole article in a few dozen lines, at the outline approval and before the expensive drafting
starts. Later, this command prints the argument of one section:

```bash
grep -h "% argues:" workspace/<slug>/manuscript/sections/introduction.tex
```

A line that names a topic ("Describes the configuration file") instead of a claim is the earliest
sign of an inventory paragraph.

## A model text

When you already have a text whose language you want, give it to the run. It can be your own
earlier version of the article or an article you like to read. The coordinator asks for it once at
intake and copies it to `workspace/<slug>/inputs/style/`. The writers then match its register: how
long its sentences are, how they open, and where it keeps its limits.

Say whether the text is your own earlier version of this article. Only then may a writer reuse its
sentences, and it changes one only as far as a fact requires. The register of any other text is
matched, and none of its sentences is copied.

## The signals script

`scripts/prose_signals.py` reads section files (`.tex`, `.txt` or `.md`), leaves out tables,
figures, listings, headings and comments, and reports where the text reads like an inventory.

```bash
uv run scripts/prose_signals.py --argues workspace/<slug>/manuscript/sections/*.tex
uv run scripts/prose_signals.py --model workspace/<slug>/inputs/style/earlier.tex sections/*.tex
```

It needs the Python standard library only, so it also runs on an article repository outside
ScieFlow: `python3 /path/to/ScieFlow/scripts/prose_signals.py sections/*.tex`.

For the inventory paragraph from the top of this page, with a topic as its argues line, it prints:

```text
                                          text
paragraphs                                   1
sentences                                    6
words                                       55
words per paragraph                       55.0
words per sentence                         9.2
sentences that state a relation             0%
negations per 1,000 words                 18.2
identifiers per 100 words                  0.0
sentences above 45 words                     0
paragraphs without an argues line            0

Signals:
  - short sentences (9.2 words on average): facts may stand alone
  - few sentences state a relation (0%)

Notes:
  - 55 words: fewer than 300, so the negation and identifier rates are not judged

Look again:
  - topic, not a claim at sections/software.tex:4 (the argues line names a topic: 'Describes the architecture'): The tool has three layers.
  - inventory run at sections/software.tex:4 (6 sentences in a row state no relation): The tool has three layers.
```

| Signal | Raised when |
| --- | --- |
| short sentences | the average is below 18 words, or below 0.8 times the model text |
| long sentences | the average is above 32 words, or above 1.25 times the model text |
| few sentences state a relation | fewer than 25 % of the sentences hold a relation word, or 12 points fewer than the model text |
| many negations | more than 12 per 1,000 words, or more than 1.5 times the model text plus 1 |
| many identifiers | more than 4 per 100 words, or more than 1.5 times the model text plus 0.5 |
| sentences above 45 words | at least one |
| paragraphs without an argues line | at least one, with `--argues` |

A relation word ties one statement to another: because, since, so that, whereas, while, although,
if, when, which, thus, therefore, allows, enables, and an opening To, By or Using. "Look again"
lists the single places: three or more sentences in a row without a relation word, a sentence above
45 words, a sentence with three or more identifiers, and an argues line that names a topic.

Options: `--model FILE` compares with a model text, `--argues` asks for the argues lines, `--json`
prints the report as JSON, and `--strict` exits with status 1 when a signal is raised. No rate is
judged for fewer than five sentences, and the negation and identifier rates need 300 words.

The script also raises "short paragraphs" when a text of three or more paragraphs averages fewer than
60 words per paragraph, which is the mark of one paragraph per claim.

### What the script cannot do

A clean report proves nothing. The script counts words, and a long sentence of facts joined by
"and" passes every count. It also cannot see whether a general statement has a case under it. Its
use is to send a reader back to the right paragraphs, so the coordinator reports what it read and
changed, and never that the prose passed.

### Where the limits come from

The limits were set on one article in four versions, so treat them as a starting point. An author
rejected the first version as hard to read. The fourth is the author's own earlier text, which the
next two versions were rebuilt on.

| | Rejected draft | Rebuild | Corrected rebuild | The author's text |
| --- | --- | --- | --- | --- |
| Sentences | 160 | 107 | 111 | 131 |
| Words per sentence | 17.5 | 27.0 | 26.3 | 25.7 |
| Sentences that state a relation | 16 % | 45 % | 47 % | 37 % |
| Negations per 1,000 words | 17.9 | 9.3 | 8.9 | 5.9 |
| Identifiers per 100 words | 0.9 | 1.2 | 1.1 | 1.2 |
| Runs of three or more sentences without a relation | 22 | 5 | 4 | 10 |

Without a model text, only the rejected draft raises the signals for short sentences, few
relations and many negations. Its paragraphs average 88 words, the rebuilds 100, the author's 177. The author's text was read from a PDF text export that keeps the
text of its tables, so its count of runs is higher than the prose alone would give. A check for
facts joined by "and" was tried and removed, because it marked the author's text as often as the
rejected draft.

## A trial with a writer model

The instructions were tried once before they were merged. One model wrote the same section from the
same invented fact sheet with the outline and draft prompts as they were, and with the prompts of
this change. There were two tasks, the software description of a recording tool and the results of
a detector comparison, with two drafts per cell.

| Task | Prompts | Words per sentence | Sentences that state a relation | Paragraphs | Words |
| --- | --- | --- | --- | --- | --- |
| Software description, 350 words asked | as they were (four drafts) | 15 to 19 | 21 to 29 % | 6 to 9 | 363 to 445 |
| | with the guide | 25 to 26 | 71 to 74 % | 3 to 4 | 428 to 490 |
| Results, 300 words asked | as they were | 18 to 19 | 33 to 37 % | 6 | 369 to 432 |
| | with the first version of the guide | 23 to 26 | 70 to 94 % | 6 | 449 to 518 |

The same facts, first with the prompts as they were and then with the guide:

> Each device runs in its own process, which only writes frames and timestamps to a ring buffer in
> shared memory. A single clock process stamps every frame with the host monotonic clock and, when
> present, a hardware trigger count. The hardware trigger needs a microcontroller board that costs
> about 30 EUR.

> Each device runs in its own process, which only writes frames and timestamps to a ring buffer in
> shared memory. A single clock process stamps every frame with the host monotonic clock and, when
> present, a hardware trigger count, so all devices share one time base. [...] when it is full, the
> oldest frames are dropped and counted, so overload appears as a number and not as silent loss.

The first version of the guide had three faults, and the trial is why the guide reads as it does
now. It produced one short paragraph per claim (11 and 14 paragraphs for 350 words, up to six of
them a single sentence). It carried a word of the task into the text ("the manifest gives no
confidence intervals"). It gave a licence a consequence that no fact supported. The guide now asks
for four to eight sentences per paragraph, names the words of the task, and says that a consequence
is a claim. The outline prompt now asks for few paragraphs. With these changes the software
description came out in three and four paragraphs. The results task was not repeated.

The reviewer persona was tried on a section that an author had rejected as a list of facts, with
two reviews per persona. With the new persona both reviews named a paragraph that lists what exists
and never says what it lets a reader do, once as a major comment. With the old persona neither did.
In all four reviews most comments still asked for more qualification of single statements.
The sentence that asks the reviewer to say in the summary what the manuscript argues was added after
these four reviews. One further review with the final wording opened its summary with that statement
and raised the listing paragraphs as a major comment.

The trial is small. It used one model, two invented tasks and two drafts per cell, and the drafts
were judged by the counts above and by the coordinator reading them, not by a human reader. Every
draft was longer than asked, and the drafts written with the guide were the longest.

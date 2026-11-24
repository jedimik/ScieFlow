# Article prose: every paragraph argues

Read this before you write or rewrite a sentence of an article. It applies to every section a
journal's reader will read. The provenance rules of your task stay as they are. This guide is about
what the checked facts are made to say.

## The reader

The reader is a researcher of the field who has never seen the data folder, the code or your notes.
They read to learn what to believe and what they can now do. They cannot ask what a fact is for, so
the text says it.

## Two failures

**Inventory** is text that reports what exists, one true statement after another. Every sentence
passes a fact check and the paragraph still says nothing, because no sentence tells the reader what
the facts mean. It is what a writer produces from an evidence list, and a fact check never objects
to it.

> The tool has three layers. The user layer holds one configuration file, the command with its four
> subcommands and the web documentation. The workflow layer is a workflow manager. The external
> tools run in containers grouped by tool family.

**Fog** is the opposite failure: general praise with no case under it.

> The tool offers a robust, flexible and user-friendly solution for reproducible analysis.

The same content as an **argument**:

> Because every output is resolved from one study file, a study can be specified, checked and
> repeated from a single document. The stages exchange only named outputs and each external tool
> runs in its own container, so a new tool can be added while the environments of the others stay
> as they are.

The four subcommands and the list of containers are still true. They moved to a table, where a
reader looks things up.

## The ladder

Every statement stands on one rung of a ladder of abstraction:

1. **State**: what exists or what was measured.
2. **Mechanism**: how it works or why it came out that way.
3. **Consequence**: what it lets the reader do, or what it changes for the question of the field.
4. **Principle**: the general idea that this is one case of.

Inventory never leaves rung 1. Fog never leaves rung 4. An article moves on the ladder inside each
paragraph: it states the claim on rung 3 or 4 and goes down to rungs 2 and 1 for what makes the
claim checkable. A general statement is sound when the paragraph, or a table or figure it names,
holds the case under it. Literal is not the same as accurate. The reader needs the idea, and the
case is its evidence.

## The paragraph

- One paragraph makes one argument: a claim, what carries it, and what follows from it. That takes
  room, usually four to eight sentences. A claim that needs one sentence is a step of a larger
  argument and belongs in the paragraph it supports.
- Above every paragraph you write, put one comment line `% argues: <the claim in one sentence>`.
  Write it first. It is a claim somebody could dispute or act on. "Describes the configuration
  file" names a topic. "One study file is all a user edits, so a study can be repeated from that
  file alone" is a claim.
- The opening sentence states the claim or sets it up, and the last sentence lands what follows.
- A fact that carries the argument stays in the paragraph. Any other fact goes into a table, a
  listing or the documentation, or it is left out.
- Keep to the length your task sets. An argued paragraph spends more words on each fact, so fewer
  facts fit in the text. Keep the ones the argument needs.

## The sentence

- A sentence joins a fact to its reason, purpose, condition, contrast or consequence: because, so
  that, whereas, while, by doing, to do, which lets. Such a sentence usually runs 20 to 35 words.
  Split one that passes 45.
- A sentence that only states a fact is in place when the next sentence uses that fact.
- Facts joined by "and" stay an inventory. Join them by what one does to the other.
- Say what the thing does and what follows from it, in the words the journal's readers use. The
  words of your notes and of your task stay there: run names, step names, ticket numbers, and
  terms such as manifest, outline, artifact or data package.

## What goes where

| Content | Place |
|---|---|
| The concept, its reason and its consequence | the running text |
| File names, configuration keys, flags, long lists of values | a table or a listing |
| A limit that changes how one result must be read | with that result, in a few words |
| Every other limit | one limitations paragraph, where the journal expects it |
| Evidence references | `% source:` comments, as your task defines them |

## Accuracy stays

Abstraction is a claim, and it is checked like one. Write nothing a reviewer could not verify from
the manuscript and its sources. When a check finds a wrong statement, correct that statement and
keep the argument around it. An objection to one sentence is a reason to fix that sentence. It is
never a reason to flatten the paragraph into a list of facts.

A consequence is a claim as well. When no fact makes it true, the fact stands alone or leaves the
text. A licence or a build tool has no consequence to invent.

## A model text

When your task gives a MODEL TEXT, its register replaces the numbers above: match its sentence
length, the way its sentences open, and where it keeps its limits. Reuse its sentences only when
the task says the text is the author's own earlier version of this article. In that case keep the
author's sentences where the facts allow, and change one only as far as a fact requires.

## Before you hand in

1. Read the `% argues:` lines of each section in order, without the paragraphs. They tell the
   section's story. Done when no line names a topic and no two lines say the same.
2. Ask "So what?" after every sentence. The answer is in that sentence or in the next one. If it
   is in neither, join the sentence to what it means, move it to a table, or cut it.
3. The script `scripts/prose_signals.py` lists the places that read like an inventory. When you
   can run commands, run `python3 scripts/prose_signals.py --argues <your section files>` from the
   repository root and read every place it lists. Each one is rewritten, or kept for a reason you
   can state. A clean report proves nothing: the script counts, and only a reader can tell whether
   a paragraph argues.

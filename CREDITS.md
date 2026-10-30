# Credits

ScieFlow's literature kernel vendors code from **researcher** (MIT License), version
1.0.0, at https://github.com/sokolmarek/researcher. The licence text is kept with the copied code in
`src/scieflow/_vendor/researcher_core/LICENSE`.

What was copied, and why: the deterministic dedupe, rank, inbound-sanitiser and query-repair
modules, which are tested upstream and are used by the search fan-out (M-C1 t4). The ORCID, ROR
and CRediT validators (an excerpt of the upstream export module), the LaTeX engine detection and
the cover-letter and response-to-reviewers templates were copied for the journal manuscript
package (M-F t1). Every
vendored file, its checksum and the single line changed in it are listed in
[VENDORED.md](VENDORED.md).

The identity-verification and monitoring modules of researcher (`verify.py`, `monitor.py`, `bib.py`,
and the `verification-report` schema) and two excerpts (`errors.py`, the connector error types;
`provenance_ts.py`, the provenance normalisers) were copied for the literature kernel's `verify-bib`
and monitor commands (M-C2 t1). Their import redirections, pins and tests are listed in
[VENDORED.md](VENDORED.md). The transport, cache, ledger, snapshot and network-mode code is
ScieFlow's own.

Three search connectors were ported from the same researcher project (MIT License, the same
licence text) for `scieflow research search pubmed`, `semantic-scholar` and `datacite` (M-C2 t3).
They are reduced ports: each keeps the parsing of the fields a paper record carries (PubMed's
esearch and efetch XML, the Semantic Scholar paper object, the DataCite JSON:API item) and the
search field lists, and none of the upstream connector's transport, key handling or async code.
The requests run through ScieFlow's Gateway. Source files: `researcher/core/researcher_core/
connectors/pubmed.py`, `semantic_scholar.py` and `datacite.py`. Their Lucene escape is the
vendored `escape_lucene` (see [VENDORED.md](VENDORED.md)).

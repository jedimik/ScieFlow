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

The identity parsers of `scieflow research verify-bib` (M-C2 t4) are reduced ports of researcher's
OpenAlex, Crossref and DataCite `to_record` functions (MIT, the same licence text). They are in
`src/scieflow/research/lib/records.py` and keep only the fields the identity verdict reads: title,
authors, issued year, DOI, arXiv id, CSL type and `extra.crossref_type`. The three identity
connectors in `src/scieflow/research/lib/identity.py` send the request shapes of researcher's
connectors (endpoints and parameters as recorded in its evaluation snapshots); they are
Gateway-backed and carry no upstream transport. Source files: `researcher/core/researcher_core/
connectors/openalex.py`, `crossref.py` and `datacite.py`.

The snowball traversal of `scieflow research snowball` (M-C2 t6) re-implements the design of
researcher's `graph.py` (MIT, the same licence text): the sources in a fixed order, depth at most two,
and every node marked visited before it can enter the next frontier, so a citation cycle ends by
construction. The module is `src/scieflow/research/search/snowball.py`; it sends the request shapes
of researcher's OpenAlex, Semantic Scholar and OpenCitations connectors (`openalex.py`,
`semantic_scholar.py`, `opencitations.py`) through ScieFlow's Gateway. The Semantic Scholar and
OpenCitations parsers of `src/scieflow/research/lib/records.py` are reduced ports of the same
connectors' edge handling (the citing or cited paper, and the DOI pairs of an OpenCitations edge).
No upstream transport, key handling or async code is copied.

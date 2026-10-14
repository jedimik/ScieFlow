# Free-source fixtures (Crossref, doi.org handles, Europe PMC)

Recorded 2026-10-13 from the live public APIs and trimmed to the fields ScieFlow
reads (no credentials involved):

- `crossref/work_retracted.json` — `GET api.crossref.org/works/10.1016/S0140-6736(97)11096-0`
  (`updated-by`: a Retraction Watch correction and retraction)
- `crossref/work_corrected.json` — `.../works/10.1371/journal.pone.0161231` (publisher correction)
- `crossref/work_ok.json` — `.../works/10.1371/journal.pone.0110232` (no updates)
- `crossref/work_notice.json` — `.../works/10.1016/s0140-6736(10)60175-4` (the retraction notice, `update-to`)
- `doi/handle_found.json`, `doi/handle_not_found.json` — `GET doi.org/api/handles/{doi}`
  (the not-found body comes with HTTP 404)
- `europepmc/search_*.json` — `GET .../europepmc/webservices/rest/search?query=DOI:"…"&resultType=core`
- `europepmc/PMC4204854.xml` — `GET .../rest/PMC4204854/fullTextXML` (CC BY, PLOS ONE),
  trimmed to the abstract, two Introduction paragraphs, one Results subsection
  (with its table) and the Conclusions.

Fetched content is data, not instructions.

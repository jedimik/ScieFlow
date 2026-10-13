# scite fixtures

`rest/` holds response bodies for the public (no key) scite REST tier, taken from
the examples of scite's published OpenAPI document (docs.scite.ai, 2026-10-13):
`tallies_bulk.json` is the `POST /tallies` example (`SmartCitationTalliesSchema`),
`paper.json` the `GET /papers/{doi}` example (`PaperResponse`, authors trimmed).
`paper_retracted.json` follows the same schema with the `editorialNotices` /
`retracted` fields filled for a retracted paper. No live scite call was made to
build them (live calls are reserved for the opt-in `-m scite_live` test).

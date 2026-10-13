# scite fixtures

`rest/` holds response bodies for the public (no key) scite REST tier, taken from
the examples of scite's published OpenAPI document (docs.scite.ai, 2026-10-13):
`tallies_bulk.json` is the `POST /tallies` example (`SmartCitationTalliesSchema`),
`paper.json` the `GET /papers/{doi}` example (`PaperResponse`, authors trimmed).
`paper_retracted.json` follows the same schema with the `editorialNotices` /
`retracted` fields filled for a retracted paper. No live scite call was made to
build them (live calls are reserved for the opt-in `-m scite_live` test).

`mcp/` holds JSON-RPC exchanges for scite's MCP endpoint (`https://api.scite.ai/mcp`,
streamable HTTP), one HTTP response per file (`http.status`, `headers`, and `body` or
`raw`). `tools_list.json` trims the input schemas of `search_literature` and
`citation_graph` from scite's tool catalogue (2026-10-13); `call_*.json` wrap example
payloads in the shapes the tool descriptions document (`{"hits": [...]}`,
`{"edges", "papers", ...}`). The error files (401, quota as a JSON-RPC error and as an
`isError` tool result, 429 with Retry-After, 503, malformed body / payload) are
constructed: scite does not document its error bodies, so classification also keys on
HTTP status. None is a live recording; the opt-in `-m scite_live` test checks the real
service.

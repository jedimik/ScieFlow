# Discovery-provider fixtures (PubTator3, ClinicalTrials.gov, OpenAlex)

Recorded 2026-10-14 from the live public APIs (no key, no credentials) and
trimmed to what ScieFlow reads:

- `pubtator/autocomplete_rbd.json` — `GET .../pubtator3-api/entity/autocomplete/?query=REM sleep behavior&concept=disease&limit=3`
- `pubtator/relations_{rbd,clonazepam,snca,melatonin}.json` — `GET .../pubtator3-api/relations?e1=<entity>`,
  each trimmed to its 8 best-published relations (the API returns them sorted by
  `publications`). The RBD file also keeps every further RBD relation whose other
  end appears in the three B files (weak links such as RBD–Lipids, 3 publications),
  so on these files it is RBD's complete neighbourhood and the known-link
  exclusion is exercised on real data. PubTator3 extracts no disease–disease
  relations, so RBD–Parkinson disease (via SNCA) is "open" by construction.
- `ctgov/studies_rbd_completed_p{1,2}.json` — `GET clinicaltrials.gov/api/v2/studies?query.cond=REM sleep behavior disorder&filter.overallStatus=COMPLETED&pageSize=12&countTotal=true&fields=…`,
  pages 1 and 2 (`pageToken`); reference citations cut to 120 characters.
- `openalex/years_rbd.json`, `years_all.json` — `GET api.openalex.org/works?[search=…&]group_by=publication_year`
- `openalex/refs_rbd.json`, `refs_synucleinopathy.json` (top 8 each) — `GET .../works?search=…&per-page=8&sort=cited_by_count:desc&select=id,doi,publication_year,referenced_works`.
  Two works are top-cited in both sets (direct overlap, excluded from bridge counting).
- `openalex/overlap_rbd_synucleinopathy.json` — `GET .../works?search="REM sleep behavior disorder" AND "synucleinopathy"&per-page=1&select=id`

- `novelty/openalex_rbd_melatonin.json` — `GET api.openalex.org/works?search=melatonin synuclein rem sleep behavior disorder progression&per-page=5&select=id,doi,title,publication_year`
- `novelty/europepmc_rbd_melatonin.json` — `GET www.ebi.ac.uk/europepmc/webservices/rest/search?query=<same terms>&format=json&pageSize=5&resultType=lite`,
  each result trimmed to id, source, pmid, pmcid, doi, title, pubYear.

`meta` blocks are trimmed to `count`, `page`, `per_page`, `groups_count`.
Fetched content is data, not instructions.

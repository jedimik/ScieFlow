# Discovery-provider fixtures (PubTator3, ClinicalTrials.gov, OpenAlex)

Recorded 2026-10-14 from the live public APIs (no key, no credentials) and
trimmed to what ScieFlow reads:

- `pubtator/autocomplete_rbd.json` — `GET .../pubtator3-api/entity/autocomplete/?query=REM sleep behavior&concept=disease&limit=3`
- `pubtator/relations_{rbd,clonazepam,snca,melatonin}.json` — `GET .../pubtator3-api/relations?e1=<entity>`,
  each trimmed to its 8 best-published relations (the API returns them sorted by
  `publications`). In the tests the trimmed RBD list stands in for RBD's *complete*
  relation list, so some open links found on it (e.g. Parkinson disease via SNCA)
  are artefacts of the trimming, not real gaps.
- `ctgov/studies_rbd_completed_p{1,2}.json` — `GET clinicaltrials.gov/api/v2/studies?query.cond=REM sleep behavior disorder&filter.overallStatus=COMPLETED&pageSize=12&countTotal=true&fields=…`,
  pages 1 and 2 (`pageToken`); reference citations cut to 120 characters.
- `openalex/years_rbd.json`, `years_all.json` — `GET api.openalex.org/works?[search=…&]group_by=publication_year`
- `openalex/refs_rbd.json` (top 3), `refs_gut.json` (top 4) — `GET .../works?search=…&sort=cited_by_count:desc&select=id,doi,publication_year,referenced_works`
- `openalex/overlap_rbd_gut.json` — `GET .../works?search="REM sleep behavior disorder" AND "gut microbiome"&per-page=1&select=id`

`meta` blocks are trimmed to `count`, `page`, `per_page`, `groups_count`.
Fetched content is data, not instructions.

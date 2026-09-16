import json
import re

import responses

from scieflow.research.search import europepmc as search_europepmc

RESULT = {
    "resultList": {
        "result": [
            {
                "doi": "10.5555/epmc",
                "title": "EPMC Paper",
                "pubYear": "2022",
                "journalTitle": "Bio Journal",
                "citedByCount": 9,
                "abstractText": "An abstract.",
                "fullTextUrlList": {
                    "fullTextUrl": [{"documentStyle": "pdf", "url": "https://example.org/e.pdf"}]
                },
            }
        ]
    }
}


@responses.activate
def test_parses_results(capsys):
    responses.get(
        re.compile(r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*"),
        json=RESULT,
    )
    search_europepmc.main(["demo", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    p = out[0]
    assert p["doi"] == "10.5555/epmc"
    assert p["year"] == 2022
    assert p["cited_by"] == 9
    assert p["url"] == "https://example.org/e.pdf"
    assert p["source"] == "europepmc"

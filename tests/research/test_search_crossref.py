import json
import re

import responses

from scieflow.research.search import crossref as search_crossref

MESSAGE = {
    "message": {
        "items": [
            {
                "DOI": "10.5555/cr",
                "title": ["Crossref Paper"],
                "published": {"date-parts": [[2021, 6, 1]]},
                "container-title": ["CR Journal"],
                "is-referenced-by-count": 33,
                "abstract": "<jats:p>Some <jats:italic>abstract</jats:italic>.</jats:p>",
                "URL": "https://doi.org/10.5555/cr",
            }
        ]
    }
}


@responses.activate
def test_parses_items_and_strips_jats(capsys):
    responses.get(re.compile(r"https://api\.crossref\.org/works.*"), json=MESSAGE)
    search_crossref.main(["demo", "--limit", "5"])
    p = json.loads(capsys.readouterr().out)[0]
    assert p["doi"] == "10.5555/cr"
    assert p["title"] == "Crossref Paper"
    assert p["year"] == 2021
    assert p["abstract"] == "Some abstract."
    assert p["cited_by"] == 33
    assert p["source"] == "crossref"


def test_norm_with_empty_date_parts():
    """Test that norm handles empty date-parts without IndexError."""
    item = {
        "DOI": "10.5555/test",
        "title": ["Test"],
        "published": {"date-parts": [[]]},
    }
    record = search_crossref.norm(item)
    assert record["year"] is None


def test_norm_with_missing_published():
    """Test that norm handles missing published key."""
    item = {
        "DOI": "10.5555/test",
        "title": ["Test"],
    }
    record = search_crossref.norm(item)
    assert record["year"] is None


def test_strip_jats_with_adjacent_tags():
    """Test that strip_jats separates words from adjacent tags with spaces."""
    text = "<jats:title>Abstract</jats:title><jats:p>Some findings.</jats:p>"
    result = search_crossref._strip_jats(text)
    assert result == "Abstract Some findings."

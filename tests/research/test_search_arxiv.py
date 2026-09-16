import json
import re

import responses

from scieflow.research.search import arxiv as search_arxiv

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Arxiv Demo  Paper</title>
    <summary>  An abstract
with a newline.  </summary>
    <published>2024-01-02T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2401.00001v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v2" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


@responses.activate
def test_parses_atom(capsys):
    responses.get(re.compile(r"https?://export\.arxiv\.org/api/query.*"), body=ATOM)
    search_arxiv.main(["demo", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    p = out[0]
    assert p["title"] == "Arxiv Demo Paper"
    assert p["year"] == 2024
    assert p["doi"] == "10.48550/arXiv.2401.00001"
    assert p["abstract"] == "An abstract with a newline."
    assert p["url"] == "http://arxiv.org/pdf/2401.00001v2"
    assert p["source"] == "arxiv"


@responses.activate
def test_from_year_filters(capsys):
    responses.get(re.compile(r"https?://export\.arxiv\.org/api/query.*"), body=ATOM)
    search_arxiv.main(["demo", "--from-year", "2025"])
    assert json.loads(capsys.readouterr().out) == []


ATOM_OLD_STYLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <title>Old Style Paper</title>
    <summary>An old paper.</summary>
    <published>1999-01-15T00:00:00Z</published>
    <link href="http://arxiv.org/abs/hep-th/9901001v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/hep-th/9901001v1" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


@responses.activate
def test_parses_old_style_arxiv_id(capsys):
    responses.get(re.compile(r"https?://export\.arxiv\.org/api/query.*"), body=ATOM_OLD_STYLE)
    search_arxiv.main(["demo", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    p = out[0]
    assert p["title"] == "Old Style Paper"
    assert p["year"] == 1999
    assert p["doi"] == "10.48550/arXiv.hep-th/9901001"
    assert p["abstract"] == "An old paper."
    assert p["url"] == "http://arxiv.org/pdf/hep-th/9901001v1"
    assert p["source"] == "arxiv"

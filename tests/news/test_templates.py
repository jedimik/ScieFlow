from scieflow.news.templates import DEFAULT_TEMPLATE, TEMPLATES, required_sections


def test_template_registry():
    assert set(TEMPLATES) == {"tool", "science", "coding", "platform", "keyword"}
    assert DEFAULT_TEMPLATE == "tool"
    for key, t in TEMPLATES.items():
        assert t.key == key
        assert t.label and t.description and t.research_focus
        assert len(t.sections) >= 4
        assert len(t.section_hints) >= 1


def test_tool_sections_match_legacy():
    assert TEMPLATES["tool"].sections == (
        "News", "Updates", "New Use Cases", "Fixes", "Improvements"
    )


def test_science_sections():
    assert TEMPLATES["science"].sections == (
        "New Papers & Preprints", "Key Findings", "Methods & Datasets",
        "Reviews & Perspectives", "Community & Events",
    )


def test_required_sections_format():
    assert required_sections("tool")[0] == "### News"
    assert required_sections("science")[-1] == "### Community & Events"


def test_suggested_timeout_defaults():
    for t in TEMPLATES.values():
        assert t.suggested_timeout >= 300
    assert TEMPLATES["science"].suggested_timeout == 900
    assert TEMPLATES["tool"].suggested_timeout == 300

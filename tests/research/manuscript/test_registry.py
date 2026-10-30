"""Paper-class registry and templates (M-F 5.11, t7): pins, placeholders, the untouched paper files."""

import hashlib
import re

import pytest

from scieflow.research.manuscript import templates
from tests.research.manuscript.t7_support import CLASSES

REGISTRY = {
    "article": {"source": "tex-core", "bibtex_style": "plainnat", "natbib": True,
                "template": "article/main.tex"},
    "elsarticle": {"source": "ctan", "package": "elsarticle", "bibtex_style": "elsarticle-num",
                   "natbib": True, "template": "elsarticle/main.tex"},
    "IEEEtran": {"source": "ctan", "package": "ieeetran", "bibtex_style": "IEEEtran",
                 "natbib": False, "template": "IEEEtran/main.tex"},
    "svjour3": {"source": "publisher", "bibtex_style": "spbasic", "natbib": True,
                "template": "svjour3/main.tex"},
}

# sha256 of the two existing paper files on the base 688075183c (they must stay byte-identical).
PAPER_SHA256 = {
    "main.tex": "07bcc56f5b314196e070d42c8b14bf64df1c547876ffd425851b86dcb34e0e61",
    "preamble.tex": "4a9c88384821ce19f8db68f0e820e6795beb34e5ad382f9629b950b4f702231d",
}


def test_registry_pins_the_four_classes_and_their_styles():
    assert templates.load_registry() == REGISTRY


def test_the_class_tuple_matches_the_registry():
    assert tuple(templates.CLASSES) == CLASSES == tuple(REGISTRY)


@pytest.mark.parametrize("cls", CLASSES)
def test_each_template_exists_and_carries_every_placeholder(cls):
    text = templates.read_template(REGISTRY[cls]["template"])
    assert set(re.findall(r"%%([A-Z]+)%%", text)) == templates.PLACEHOLDERS


def test_templates_contain_no_placeholder_outside_the_pinned_set():
    for cls in CLASSES:
        text = templates.read_template(REGISTRY[cls]["template"])
        assert set(re.findall(r"%%[^%\n]*%%", text)) <= {f"%%{p}%%" for p in templates.PLACEHOLDERS}


def test_no_class_file_is_vendored_into_the_templates():
    assert list(templates.TEMPLATE_DIR.rglob("*.cls")) == []
    assert list(templates.TEMPLATE_DIR.rglob("*.sty")) == []


def test_the_existing_paper_files_are_byte_identical_to_base():
    for name, digest in PAPER_SHA256.items():
        data = (templates.TEMPLATE_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name


def test_a_registry_missing_a_class_is_refused(tmp_path):
    text = "\n".join(f"{k}: {{source: x, bibtex_style: y, natbib: true, template: {k}/main.tex}}"
                     for k in ("article", "elsarticle", "IEEEtran")) + "\n"
    (tmp_path / "registry.yml").write_text(text, encoding="utf-8")
    with pytest.raises(templates.TemplateError):
        templates.load_registry(tmp_path)

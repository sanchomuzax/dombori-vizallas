"""Shared pytest configuration: src/ on sys.path, fixture-file loaders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def table_html_bytes() -> bytes:
    """Raw bytes of the Hydroinfo forecast table fixture (as served: ISO-8859-2)."""
    return (FIXTURES_DIR / "442540H_table.html").read_bytes()


@pytest.fixture
def table_html_text(table_html_bytes: bytes) -> str:
    """Table fixture decoded the way ``hydroinfo.fetch_table`` decodes it."""
    return table_html_bytes.decode("iso-8859-2")


@pytest.fixture
def imagemap_html_bytes() -> bytes:
    """Raw bytes of the Hydroinfo imagemap fallback fixture.

    The file on disk is genuinely UTF-8, but the real server mis-declares/
    mis-decodes it as ISO-8859-2 (see ``hydroinfo._fix_mojibake``), so we
    decode it here the same (wrong) way ``fetch_imagemap`` does -- the
    resulting mojibake text is what ``parse_imagemap`` is designed to fix.
    """
    return (FIXTURES_DIR / "442540H_imagemap.html").read_bytes()


@pytest.fixture
def imagemap_html_text(imagemap_html_bytes: bytes) -> str:
    return imagemap_html_bytes.decode("iso-8859-2")


@pytest.fixture
def tsshortlist_payload() -> list[dict]:
    """Parsed JSON of the vizugy TsShortList sample response."""
    with (FIXTURES_DIR / "tsshortlist_sample.json").open(encoding="utf-8") as fh:
        return json.load(fh)

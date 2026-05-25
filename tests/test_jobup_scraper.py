from __future__ import annotations

from datetime import date
from pathlib import Path

from src.scrapers.base import OfferStub
from src.scrapers.jobup import build_search_url, parse_offer_html, parse_search_html

FIXTURE = Path("tests/fixtures/jobup_listing.html")


def test_build_search_url_encodes_query() -> None:
    url = build_search_url("conseiller de vente", "La Chaux-de-Fonds")

    assert "term=conseiller+de+vente" in url
    assert "location=La+Chaux-de-Fonds" in url


def test_parse_search_html_extracts_unique_stubs() -> None:
    html = FIXTURE.read_text(encoding="utf-8")

    stubs = parse_search_html(html)

    assert len(stubs) == 2
    assert stubs[0].source == "jobup"
    assert stubs[0].title == "Vendeuse / Vendeur LANDI (f/h/d)"
    assert str(stubs[0].url) == "https://www.jobup.ch/fr/emplois/detail/123456/"


def test_parse_offer_html_prefers_json_ld() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    stub = OfferStub(
        source="jobup",
        url="https://www.jobup.ch/fr/emplois/detail/123456/",
        title="Fallback title",
    )

    offer = parse_offer_html(html, fallback_stub=stub)

    assert offer.source == "jobup"
    assert offer.title == "Vendeuse / Vendeur LANDI (f/h/d)"
    assert offer.company == "LANDI Région Neuchâtel SA"
    assert offer.location == "2300 La Chaux-de-Fonds NE"
    assert offer.postal_code == "2300"
    assert offer.publication_date == date(2026, 5, 24)
    assert "Permis G accepté" in offer.raw_description

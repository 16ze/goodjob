from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import HttpUrl

from src.scrapers.jobs import (
    JobsHTMLParser,
    is_job_detail_url,
    offer_from_parts,
    parse_offer_html,
    parse_search_html,
    build_search_url,
    sha256_text,
)
from src.scrapers.base import OfferStub

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def listing_html() -> str:
    return (FIXTURES / "jobs_listing.html").read_text(encoding="utf-8")


@pytest.fixture()
def detail_html() -> str:
    return (FIXTURES / "jobs_detail.html").read_text(encoding="utf-8")


class TestIsJobDetailUrl:
    def test_detail_url_accepted(self) -> None:
        assert is_job_detail_url("/fr/offres-demploi/vendeur-conseil-2845-le-noirmont-12345/")

    def test_list_url_rejected(self) -> None:
        assert not is_job_detail_url("/fr/offres-demploi/")

    def test_list_url_with_query_rejected(self) -> None:
        assert not is_job_detail_url("/fr/offres-demploi/?term=vendeur&location=jura")

    def test_absolute_detail_url_accepted(self) -> None:
        assert is_job_detail_url(
            "https://www.jobs.ch/fr/offres-demploi/vendeur-2800-delemont-99/"
        )

    def test_other_path_rejected(self) -> None:
        assert not is_job_detail_url("/fr/entreprises/acme/")


class TestBuildSearchUrl:
    def test_encodes_term_and_location(self) -> None:
        url = build_search_url("vendeur", "jura")
        assert "term=vendeur" in url
        assert "location=jura" in url

    def test_encodes_accents(self) -> None:
        url = build_search_url("vendeur", "neuchâtel")
        assert "neuch%C3%A2tel" in url

    def test_points_to_jobs_ch(self) -> None:
        url = build_search_url("vendeur", "jura")
        assert url.startswith("https://www.jobs.ch")


class TestParseSearchHtml:
    def test_extracts_three_stubs(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert len(stubs) == 3

    def test_stubs_have_correct_source(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert all(s.source == "jobs.ch" for s in stubs)

    def test_stubs_have_absolute_urls(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        for stub in stubs:
            assert str(stub.url).startswith("https://www.jobs.ch")

    def test_titles_not_empty(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert all(s.title for s in stubs)

    def test_deduplicates_urls(self) -> None:
        html = """
        <a href="/fr/offres-demploi/vendeur-2800-12345/">Vendeur</a>
        <a href="/fr/offres-demploi/vendeur-2800-12345/">Vendeur</a>
        """
        stubs = parse_search_html(html)
        assert len(stubs) == 1


class TestParseOfferHtml:
    def test_extracts_from_json_ld(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert offer.title == "Vendeur-conseil (h/f)"
        assert offer.company == "Montre SA"
        assert offer.postal_code == "2845"
        assert offer.publication_date == date(2026, 5, 20)

    def test_source_is_jobs_ch(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert offer.source == "jobs.ch"

    def test_description_not_empty(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert len(offer.raw_description) > 10

    def test_fallback_to_stub_when_no_json_ld(self) -> None:
        html = "<html><body><p>Poste ouvert à Porrentruy 2900.</p></body></html>"
        stub = OfferStub(
            source="jobs.ch",
            url=HttpUrl("https://www.jobs.ch/fr/offres-demploi/test-99999/"),
            title="Vendeur Porrentruy",
            company="ACME",
            location="Porrentruy",
        )
        offer = parse_offer_html(html, fallback_stub=stub)
        assert offer.title == "Vendeur Porrentruy"
        assert offer.company == "ACME"

    def test_url_hash_is_stable(self, detail_html: str) -> None:
        offer1 = parse_offer_html(detail_html)
        offer2 = parse_offer_html(detail_html)
        assert offer1.url_hash == offer2.url_hash

    def test_url_hash_matches_sha256_of_url(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert offer.url_hash == sha256_text(str(offer.url))


class TestOfferFromParts:
    def test_extracts_postal_code_from_location(self) -> None:
        offer = offer_from_parts(
            url="https://www.jobs.ch/fr/offres-demploi/test-12345/",
            title="Vendeur",
            raw_description="Poste à pourvoir.",
            location="2800 Delémont",
        )
        assert offer.postal_code == "2800"

    def test_extracts_postal_code_from_description(self) -> None:
        offer = offer_from_parts(
            url="https://www.jobs.ch/fr/offres-demploi/test-12345/",
            title="Vendeur",
            raw_description="Notre magasin est situé à 2610 Saint-Imier.",
        )
        assert offer.postal_code == "2610"

    def test_normalizes_whitespace_in_title(self) -> None:
        offer = offer_from_parts(
            url="https://www.jobs.ch/fr/offres-demploi/test-12345/",
            title="  Vendeur   conseil  ",
            raw_description="Description.",
        )
        assert offer.title == "Vendeur conseil"

    def test_relative_url_gets_base_url(self) -> None:
        offer = offer_from_parts(
            url="/fr/offres-demploi/test-12345/",
            title="Vendeur",
            raw_description="Description.",
        )
        assert str(offer.url).startswith("https://www.jobs.ch")


class TestJobsHTMLParser:
    def test_parses_json_ld_blocks(self, detail_html: str) -> None:
        parser = JobsHTMLParser()
        parser.feed(detail_html)
        assert len(parser.json_ld_blocks) == 1

    def test_parses_og_title(self, detail_html: str) -> None:
        parser = JobsHTMLParser()
        parser.feed(detail_html)
        assert parser.meta.get("og:title") == "Vendeur-conseil (h/f)"

    def test_visible_text_not_empty(self, detail_html: str) -> None:
        parser = JobsHTMLParser()
        parser.feed(detail_html)
        assert len(parser.visible_text) > 0

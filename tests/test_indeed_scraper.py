from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import HttpUrl

from src.scrapers.base import OfferStub
from src.scrapers.indeed import (
    IndeedHTMLParser,
    build_search_url,
    canonicalize_indeed_url,
    is_job_detail_url,
    offer_from_parts,
    parse_ago_date,
    parse_offer_html,
    parse_search_html,
    sha256_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def listing_html() -> str:
    return (FIXTURES / "indeed_listing.html").read_text(encoding="utf-8")


@pytest.fixture()
def detail_html() -> str:
    return (FIXTURES / "indeed_detail.html").read_text(encoding="utf-8")


class TestIsJobDetailUrl:
    def test_rc_clk_accepted(self) -> None:
        assert is_job_detail_url("/rc/clk?jk=abc123&fccid=xyz")

    def test_viewjob_accepted(self) -> None:
        assert is_job_detail_url("/viewjob?jk=abc123")

    def test_pagead_accepted(self) -> None:
        assert is_job_detail_url("/pagead/clk?jk=abc123")

    def test_search_page_rejected(self) -> None:
        assert not is_job_detail_url("/jobs?q=vendeur&l=jura")

    def test_homepage_rejected(self) -> None:
        assert not is_job_detail_url("https://ch.indeed.com/")


class TestCanonicalizeIndeedUrl:
    def test_extracts_jk_from_rc_clk(self) -> None:
        url = canonicalize_indeed_url("/rc/clk?jk=abc123def456&fccid=xyz&vjs=3")
        assert url == "https://ch.indeed.com/viewjob?jk=abc123def456"

    def test_extracts_jk_from_viewjob(self) -> None:
        url = canonicalize_indeed_url("/viewjob?jk=ddd444eee555&from=serp")
        assert url == "https://ch.indeed.com/viewjob?jk=ddd444eee555"

    def test_same_jk_produces_same_url(self) -> None:
        url1 = canonicalize_indeed_url("/rc/clk?jk=abc123&fccid=aaa")
        url2 = canonicalize_indeed_url("/rc/clk?jk=abc123&fccid=bbb")
        assert url1 == url2

    def test_absolute_url_passthrough(self) -> None:
        url = canonicalize_indeed_url("https://ch.indeed.com/viewjob?jk=xyz789")
        assert url == "https://ch.indeed.com/viewjob?jk=xyz789"


class TestBuildSearchUrl:
    def test_encodes_term_and_location(self) -> None:
        url = build_search_url("vendeur", "jura")
        assert "q=vendeur" in url
        assert "l=jura" in url

    def test_sorts_by_date(self) -> None:
        url = build_search_url("vendeur", "jura")
        assert "sort=date" in url

    def test_points_to_indeed_ch(self) -> None:
        url = build_search_url("vendeur", "jura")
        assert url.startswith("https://ch.indeed.com")

    def test_encodes_accents(self) -> None:
        url = build_search_url("vendeur", "neuchâtel")
        assert "neuch%C3%A2tel" in url


class TestParseSearchHtml:
    def test_extracts_three_stubs(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert len(stubs) == 3

    def test_stubs_have_correct_source(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert all(s.source == "indeed" for s in stubs)

    def test_urls_are_canonicalized(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        for stub in stubs:
            assert "viewjob?jk=" in str(stub.url)

    def test_deduplicates_same_jk(self) -> None:
        html = """
        <a href="/rc/clk?jk=abc123&fccid=aaa">Vendeur A</a>
        <a href="/rc/clk?jk=abc123&fccid=bbb">Vendeur A bis</a>
        """
        stubs = parse_search_html(html)
        assert len(stubs) == 1

    def test_titles_not_empty(self, listing_html: str) -> None:
        stubs = parse_search_html(listing_html)
        assert all(s.title for s in stubs)


class TestParseOfferHtml:
    def test_extracts_from_json_ld(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert offer.title == "Vendeur-conseil (h/f) 100%"
        assert offer.company == "Bijouterie Jurassienne"
        assert offer.postal_code == "2800"
        assert offer.publication_date == date(2026, 5, 22)

    def test_source_is_indeed(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert offer.source == "indeed"

    def test_description_not_empty(self, detail_html: str) -> None:
        offer = parse_offer_html(detail_html)
        assert len(offer.raw_description) > 10

    def test_fallback_to_stub_when_no_json_ld(self) -> None:
        html = "<html><body><p>Poste ouvert à Porrentruy 2900.</p></body></html>"
        stub = OfferStub(
            source="indeed",
            url=HttpUrl("https://ch.indeed.com/viewjob?jk=test99999"),
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


class TestParseAgoDate:
    def test_today(self) -> None:
        assert parse_ago_date("Publié aujourd'hui") == date.today()

    def test_yesterday(self) -> None:
        assert parse_ago_date("hier") == date.today() - timedelta(days=1)

    def test_n_days_fr(self) -> None:
        assert parse_ago_date("il y a 3 jours") == date.today() - timedelta(days=3)

    def test_n_days_en(self) -> None:
        assert parse_ago_date("3 days ago") == date.today() - timedelta(days=3)

    def test_unknown_returns_none(self) -> None:
        assert parse_ago_date("some random text") is None


class TestOfferFromParts:
    def test_extracts_postal_code_from_location(self) -> None:
        offer = offer_from_parts(
            url="https://ch.indeed.com/viewjob?jk=test123",
            title="Vendeur",
            raw_description="Poste à pourvoir.",
            location="2800 Delémont",
        )
        assert offer.postal_code == "2800"

    def test_extracts_postal_code_from_description(self) -> None:
        offer = offer_from_parts(
            url="https://ch.indeed.com/viewjob?jk=test123",
            title="Vendeur",
            raw_description="Notre magasin est situé à 2610 Saint-Imier.",
        )
        assert offer.postal_code == "2610"

    def test_normalizes_whitespace_in_title(self) -> None:
        offer = offer_from_parts(
            url="https://ch.indeed.com/viewjob?jk=test123",
            title="  Vendeur   conseil  ",
            raw_description="Description.",
        )
        assert offer.title == "Vendeur conseil"

    def test_relative_url_gets_base_url(self) -> None:
        offer = offer_from_parts(
            url="/viewjob?jk=test123",
            title="Vendeur",
            raw_description="Description.",
        )
        assert str(offer.url).startswith("https://ch.indeed.com")


class TestIndeedHTMLParser:
    def test_parses_json_ld_blocks(self, detail_html: str) -> None:
        parser = IndeedHTMLParser()
        parser.feed(detail_html)
        assert len(parser.json_ld_blocks) == 1

    def test_parses_og_title(self, detail_html: str) -> None:
        parser = IndeedHTMLParser()
        parser.feed(detail_html)
        assert parser.meta.get("og:title") == "Vendeur-conseil (h/f) 100%"

    def test_visible_text_not_empty(self, detail_html: str) -> None:
        parser = IndeedHTMLParser()
        parser.feed(detail_html)
        assert len(parser.visible_text) > 0

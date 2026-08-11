"""Unit tests for ETR Market Terminal HTML parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.etr.parser import html_to_lines, parse_analysis_html, parse_number

FIXTURE = Path(__file__).parent / "fixtures" / "etr_btc.html"


def test_parse_number_thousands_and_decimal() -> None:
    assert parse_number("63,715.4") == pytest.approx(63715.4)
    assert parse_number("63900.6") == pytest.approx(63900.6)
    assert parse_number("72") == pytest.approx(72.0)


def test_html_to_lines_fixture() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    lines = html_to_lines(html)
    assert any("Context score" in line for line in lines)
    assert any("Sesgo" in line for line in lines)


def test_parse_btc_fixture_core_fields() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    report = parse_analysis_html(html, "btc")

    assert report.asset == "btc"
    assert report.label == "Bitcoin"
    assert report.price == pytest.approx(63715.4)
    assert report.context_score == pytest.approx(72.0)
    assert report.bias.lower() == "bajista"
    assert "reacción" in report.estado.lower() or "reaccion" in report.estado.lower()
    assert report.lectura_headline
    assert report.primary is not None
    assert report.primary.direction.lower().startswith("baj")
    assert report.primary.activation_zone is not None
    assert report.primary.invalidation == pytest.approx(64131.2)
    assert report.primary.tp1 == pytest.approx(63261.3)
    assert report.alternative is not None
    assert report.alternative.direction.lower().startswith("alc")
    assert report.fingerprint()


def test_price_in_primary_zone() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    report = parse_analysis_html(html, "btc")
    # Primary zone is ~63900–64131; price 63715 is outside
    assert report.price_in_primary_zone() is False

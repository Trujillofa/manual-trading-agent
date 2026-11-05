"""The ETR analysis page is client-rendered; these fixtures are real renders.

tests/fixtures/etr_rendered_btc.txt    — a page with a live analysis (BTC, 92/100)
tests/fixtures/etr_rendered_gold_unavailable.txt — gold while its M5 history is stale
"""

from pathlib import Path

import pytest

from src.etr.parser import EtrUnavailableError, parse_rendered_text

FIXTURES = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_reads_the_report_from_the_rendered_page() -> None:
    report = parse_rendered_text(_text("etr_rendered_btc.txt"), "btc")

    assert report.context_score == 92
    assert report.bias == "Bajista"
    assert report.estado == "Esperando Confirmación"
    assert report.h4_context == "Bajista"
    assert report.m5_execution == "Bajista"
    assert report.structure == "Bajista"
    assert report.lectura_headline.startswith("Bitcoin")
    assert report.price == pytest.approx(75972.25)


def test_the_primary_scenario_keeps_its_zone_and_invalidation() -> None:
    report = parse_rendered_text(_text("etr_rendered_btc.txt"), "btc")

    assert report.primary is not None
    assert report.primary.direction.lower() == "bajista"
    assert report.primary.role == "Principal"
    assert report.primary.activation_zone is not None
    low, high = report.primary.activation_zone.low, report.primary.activation_zone.high
    assert low == pytest.approx(80578.66, rel=1e-3)
    assert high == pytest.approx(81189.63, rel=1e-3)
    assert report.primary.invalidation == pytest.approx(81189.63)
    assert report.primary.score == pytest.approx(100.0)
    assert report.price_in_primary_zone() is False


def test_a_page_without_scenarios_is_unavailable_not_a_parse_error() -> None:
    """ETR withholds scenarios until M5 history is current and says so on the page.

    Reporting that as a parse failure (the old behaviour) blamed this agent for
    the terminal's own gate and told nobody anything actionable.
    """
    with pytest.raises(EtrUnavailableError) as excinfo:
        parse_rendered_text(_text("etr_rendered_gold_unavailable.txt"), "gold")

    assert "gold" in str(excinfo.value)


def test_an_authenticated_page_without_an_analysis_section_is_unavailable() -> None:
    """NASDAQ currently renders the app shell with no candles and no notice."""
    shell = "ETR EDGE / EXNESS\n\nAnálisis técnico\n\nTus gráficos, las cotizaciones de tu cuenta y los escenarios H4 / M5 en un solo lugar."

    with pytest.raises(EtrUnavailableError):
        parse_rendered_text(shell, "nasdaq")


def test_a_page_that_never_loaded_is_still_an_error() -> None:
    with pytest.raises(ValueError, match="did not load"):
        parse_rendered_text("Iniciar sesión\nCorreo\nContraseña", "btc")

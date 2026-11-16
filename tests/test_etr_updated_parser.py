"""The "Actualizado" stamp must be read (and validated), inline or on its own line.

The defect this guards: the parser returned *the line after* the label, so an
inline ``Actualizado: 11/08/2026 14:30`` yielded the next section title
("Tutorial"). ``source_time`` cannot parse that, and the monotonic guard fails
open on unparseable values, so it was silently inert for every page that rendered
the inline form.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.etr.parser import parse_analysis_html, parse_rendered_text, parse_updated_at


def test_inline_stamp_is_returned() -> None:
    lines = ["Bitcoin · contexto bajista en 4H", "Actualizado: 11/08/2026 14:30", "Tutorial"]
    assert parse_updated_at(lines) == "11/08/2026 14:30"


def test_inline_stamp_without_colon_is_returned() -> None:
    lines = ["Actualizado 11/08/2026", "Tutorial"]
    assert parse_updated_at(lines) == "11/08/2026"


def test_header_stamp_on_the_next_line_is_returned() -> None:
    assert parse_updated_at(["Actualizado:", "11/08/2026 14:30", "Tutorial"]) == "11/08/2026 14:30"


def test_label_followed_by_a_section_title_yields_none() -> None:
    # The old implementation returned "Tutorial" here and the guard went inert.
    assert parse_updated_at(["Actualizado:", "Tutorial", "Noticias"]) is None
    assert parse_updated_at(["Actualizado", "Tutorial"]) is None


def test_missing_label_yields_none() -> None:
    assert parse_updated_at(["Bitcoin", "Tutorial"]) is None


def test_rendered_text_sets_updated_at() -> None:
    text = "\n".join(
        [
            "Mercado · BTCUSD",
            "Bitcoin · contexto bajista en 4H",
            "Actualizado: 11/08/2026 14:30",
            "Context score 42",
            "Tutorial",
        ]
    )
    assert parse_rendered_text(text, "btc").updated_at == "11/08/2026 14:30"


def test_html_inline_stamp_sets_updated_at() -> None:
    lines = [
        line
        for line in (Path(__file__).parent / "fixtures" / "etr_rendered_btc.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    lines.insert(0, "Actualizado: 11/08/2026 14:30")
    html = "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"

    report = parse_analysis_html(html, "btc")

    assert report.updated_at == "11/08/2026 14:30"


@pytest.mark.parametrize("label", ["Actualizado", "Actualizada", "actualizado:"])
def test_label_variants(label: str) -> None:
    assert parse_updated_at([f"{label} 11/08/2026", "Tutorial"]) == "11/08/2026"

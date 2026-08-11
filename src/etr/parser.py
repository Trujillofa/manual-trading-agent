"""Parse ETR Market Terminal HTML into structured reports."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from src.etr.models import ASSET_LABELS, EtrReport, EtrScenario, PriceZone

# Prefer full integer+optional-decimal runs so 64131.2 is not truncated to 641.
_NUM_TOKEN = r"\d+(?:[.,]\d+)*"
_RANGE_RE = re.compile(
    rf"(?P<lo>{_NUM_TOKEN})\s*[–\-—]\s*(?P<hi>{_NUM_TOKEN})"
)
_NUM_RE = re.compile(_NUM_TOKEN)
_CENTER_RE = re.compile(rf"centro\s+({_NUM_TOKEN})", re.I)
_INVALIDATION_RE = re.compile(
    rf"invalida(?:ción|cion)?[^\d]{{0,60}}?({_NUM_TOKEN})",
    re.I,
)
_SCORE_RE = re.compile(rf"(?:puntuaci[oó]n|score)[^\d]{{0,40}}?({_NUM_TOKEN})", re.I)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


def html_to_lines(html: str) -> list[str]:
    """Extract visible text lines from HTML, deduping consecutive equals."""
    parser = _TextExtractor()
    parser.feed(html)
    out: list[str] = []
    for part in parser.parts:
        # Normalize nbsp and thin spaces
        cleaned = part.replace("\xa0", " ").replace("\u202f", " ").strip()
        if cleaned and (not out or out[-1] != cleaned):
            out.append(cleaned)
    return out


def parse_number(text: str) -> float | None:
    """Parse numbers that may use thousands separators or comma decimals."""
    raw = text.strip().replace(" ", "")
    if not raw:
        return None
    # 63,715.4 or 63.715,4 or 63900.6 or 64131.2
    if "," in raw and "." in raw:
        if raw.rfind(".") > raw.rfind(","):
            # US style thousands: 63,715.4
            raw = raw.replace(",", "")
        else:
            # EU style: 63.715,4
            raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].replace(".", "").isdigit():
            # thousands only: 63,715
            raw = raw.replace(",", "")
        elif all(len(p) == 3 for p in parts[1:]) and parts[0].isdigit():
            raw = raw.replace(",", "")
        else:
            # decimal comma: 34,45
            raw = raw.replace(",", ".")
    # Multiple dots without comma: treat last as decimal only if short fraction
    if raw.count(".") > 1:
        head, _, tail = raw.rpartition(".")
        if tail.isdigit() and len(tail) <= 4 and head.replace(".", "").isdigit():
            raw = head.replace(".", "") + "." + tail
    try:
        return float(raw)
    except ValueError:
        return None


def _find_after(lines: list[str], label: str, *, max_ahead: int = 3) -> str | None:
    label_l = label.lower()
    for i, line in enumerate(lines):
        if line.lower() == label_l or line.lower().startswith(label_l + " "):
            for j in range(1, max_ahead + 1):
                if i + j < len(lines):
                    candidate = lines[i + j].strip()
                    if candidate and candidate.lower() != label_l:
                        return candidate
    return None


def _find_index(lines: list[str], *needles: str, exact: bool = False) -> int | None:
    lowered = [n.lower() for n in needles]
    for i, line in enumerate(lines):
        ll = line.lower().strip()
        if exact:
            if any(ll == n or ll.rstrip(":") == n for n in lowered):
                return i
        elif any(n in ll for n in lowered):
            return i
    return None


def _find_label_index(lines: list[str], *labels: str) -> int | None:
    """Prefer short label lines (section headers) over long narrative mentions."""
    exact = _find_index(lines, *labels, exact=True)
    if exact is not None:
        return exact
    # Fallback: short lines that start with the label
    lowered = [n.lower() for n in labels]
    for i, line in enumerate(lines):
        ll = line.lower().strip()
        if len(ll) > 48:
            continue
        if any(ll == n or ll.startswith(n) for n in lowered):
            return i
    return None


def _parse_zone(text: str) -> PriceZone | None:
    match = _RANGE_RE.search(text.replace(" ", ""))
    if not match:
        match = _RANGE_RE.search(text)
    if not match:
        return None
    lo = parse_number(match.group("lo"))
    hi = parse_number(match.group("hi"))
    if lo is None or hi is None:
        return None
    return PriceZone(low=lo, high=hi)


def _first_number(text: str) -> float | None:
    match = _NUM_RE.search(text)
    if not match:
        return None
    return parse_number(match.group(0))


def _slice_block(lines: list[str], start: int, end: int) -> list[str]:
    return lines[start:end]


def _parse_scenario(block: list[str], role: str) -> EtrScenario | None:
    if not block:
        return None
    text = "\n".join(block)
    name = block[0] if block else role
    direction = ""
    status = ""
    for line in block[:12]:
        ll = line.lower()
        if ll in {"bajista", "alcista"}:
            if not direction:
                direction = line.strip().capitalize()
            continue
        if not status and (
            "esperando" in ll
            or "confirmaci" in ll
            or ll == "activo"
            or "contra tendencia" in ll
        ):
            status = line.strip()

    activation: PriceZone | None = None
    for line in block:
        if "dentro de" in line.lower() or "zona" in line.lower() or "activaci" in line.lower():
            zone = _parse_zone(line)
            if zone is not None:
                activation = zone
                break
    if activation is None:
        for line in block:
            zone = _parse_zone(line)
            if zone is not None and ("–" in line or "-" in line or "—" in line):
                activation = zone
                break

    invalidation = None
    inv_match = _INVALIDATION_RE.search(text)
    if inv_match:
        invalidation = parse_number(inv_match.group(1))

    centers = [parse_number(m.group(1)) for m in _CENTER_RE.finditer(text)]
    centers = [c for c in centers if c is not None]
    tp1 = centers[0] if len(centers) >= 1 else None
    tp2 = centers[1] if len(centers) >= 2 else None

    score = None
    score_match = _SCORE_RE.search(text)
    if score_match:
        score = parse_number(score_match.group(1))

    return EtrScenario(
        name=name,
        direction=direction or "Unknown",
        status=status,
        role=role,
        activation_zone=activation,
        invalidation=invalidation,
        tp1=tp1,
        tp2=tp2,
        score=score,
    )


def parse_analysis_html(html: str, asset: str) -> EtrReport:
    """Parse Market Terminal HTML for one asset slug (btc/gold/nasdaq/oil)."""
    lines = html_to_lines(html)
    if not lines:
        raise ValueError(f"ETR parse failed for {asset}: empty page text")

    label = ASSET_LABELS.get(asset, asset.upper())

    # Price: first number-like line after the asset label near Market Terminal
    price: float | None = None
    terminal_idx = _find_index(lines, "market terminal")
    search_from = terminal_idx if terminal_idx is not None else 0
    for i in range(search_from, min(search_from + 40, len(lines))):
        if lines[i].lower() in {label.lower(), "bitcoin", "oro", "nasdaq", "petróleo", "petroleo"}:
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = parse_number(lines[j].replace(",", ""))
                # also try with comma as thousands
                if candidate is None:
                    candidate = parse_number(lines[j])
                if candidate is not None and candidate > 1:
                    price = candidate
                    break
            if price is not None:
                break
    if price is None:
        # Fallback: first large number after Context score header area
        for line in lines:
            num = parse_number(line)
            if num is not None and num > 100:
                price = num
                break

    updated_at = _find_after(lines, "Actualizado:")
    context_score = None
    score_line = _find_after(lines, "Context score")
    if score_line:
        context_score = parse_number(score_line)
    if context_score is None:
        idx = _find_index(lines, "context score")
        if idx is not None and idx + 1 < len(lines):
            context_score = parse_number(lines[idx + 1])

    bias = _find_after(lines, "Sesgo") or ""
    estado = _find_after(lines, "Estado") or ""

    lectura_headline = ""
    lectura_body = ""
    lectura_idx = _find_index(lines, "lectura actual")
    if lectura_idx is not None:
        # Next substantial lines
        for j in range(lectura_idx + 1, min(lectura_idx + 6, len(lines))):
            line = lines[j]
            if line.lower() in {"contexto 4h", "ejecución 5m", "ejecucion 5m", "estructura"}:
                break
            if len(line) > 40 and not lectura_headline:
                lectura_headline = line
            elif len(line) > 40 and lectura_headline and not lectura_body:
                lectura_body = line
                break

    h4 = ""
    m5 = ""
    structure = ""
    h4_idx = _find_label_index(lines, "contexto 4h")
    if h4_idx is not None and h4_idx + 1 < len(lines):
        h4 = lines[h4_idx + 1]
        if h4_idx + 2 < len(lines) and "EMA" in lines[h4_idx + 2]:
            h4 = f"{h4} · {lines[h4_idx + 2]}"
    m5_idx = _find_label_index(lines, "ejecución 5m", "ejecucion 5m")
    if m5_idx is not None and m5_idx + 1 < len(lines):
        m5 = lines[m5_idx + 1]
        if m5_idx + 2 < len(lines) and "EMA" in lines[m5_idx + 2]:
            m5 = f"{m5} · {lines[m5_idx + 2]}"
    st_idx = _find_label_index(lines, "estructura")
    if st_idx is not None and st_idx + 1 < len(lines):
        structure = lines[st_idx + 1]
        if st_idx + 2 < len(lines) and "BOS" in lines[st_idx + 2].upper():
            structure = f"{structure} · {lines[st_idx + 2]}"

    primary_idx = _find_label_index(lines, "escenario principal")
    alt_idx = _find_label_index(lines, "escenario alternativo")
    primary: EtrScenario | None = None
    alternative: EtrScenario | None = None
    if primary_idx is not None:
        end = alt_idx if alt_idx is not None and alt_idx > primary_idx else min(
            primary_idx + 45, len(lines)
        )
        # Name often sits right after the header
        block = _slice_block(lines, primary_idx + 1, end)
        primary = _parse_scenario(block, "Principal")
    if alt_idx is not None:
        block = _slice_block(lines, alt_idx + 1, min(alt_idx + 45, len(lines)))
        alternative = _parse_scenario(block, "Alternativo")

    if not bias and not estado and context_score is None and primary is None:
        raise ValueError(
            f"ETR parse failed for {asset}: missing core fields "
            f"(bias/estado/score/primary). lines={len(lines)}"
        )

    excerpt = " | ".join(lines[:80])[:500]
    return EtrReport(
        asset=asset,
        label=label,
        price=price,
        updated_at=updated_at,
        context_score=context_score,
        bias=bias,
        estado=estado,
        lectura_headline=lectura_headline,
        lectura_body=lectura_body,
        h4_context=h4,
        m5_execution=m5,
        structure=structure,
        primary=primary,
        alternative=alternative,
        raw_text_excerpt=excerpt,
    )

"""Tests for the PEAD source audit inventory."""

from __future__ import annotations

from pathlib import Path

from research.new_edge.pead.data.audit_pead_sources import (
    FieldMatrixRow,
    PeadSourceAudit,
    SourceCandidate,
    _desk_zacks,
    _matrix_present_label,
    _probe_alpha_vantage,
    _probe_eodhd,
    _probe_fmp,
    _probe_twelve_data,
    _verdict_from_matrix,
    build_manifest,
    evaluate_candidates,
)


def test_verdict_from_matrix_marks_missing_required_field_insufficient() -> None:
    rows = (
        FieldMatrixRow("estimate_observation_timestamp", False, "missing"),
        FieldMatrixRow("consensus_eps", True, "present"),
    )
    assert _verdict_from_matrix(rows) == "INSUFFICIENT"


def test_evaluate_candidates_blocks_without_local_snapshot() -> None:
    audit, candidates = evaluate_candidates(local_snapshots=[], prod_paths=[])

    assert audit.verdict == "BLOCKED"
    assert audit.local_snapshots_found == 0
    assert audit.data_pass_candidates == 0
    assert candidates
    assert all(candidate.verdict != "DATA_PASS" for candidate in candidates)


def test_probe_eodhd_blocks_earnings_on_free_tier_or_missing_key() -> None:
    candidate = _probe_eodhd()

    if not candidate.probe_evidence.get("user_key_present"):
        assert candidate.verdict == "UNVERIFIED"
        return

    assert candidate.verdict == "INSUFFICIENT"
    assert candidate.probe_evidence.get("calendar_earnings_symbol_probe") == "free_tier_eod_only"
    assert any("free EOD-only" in gap or "earnings" in gap for gap in candidate.blocking_gaps)


def test_matrix_present_label_marks_desk_review_as_marketed_unverified() -> None:
    candidate = _desk_zacks()
    row = FieldMatrixRow("announcement_timestamp_tz", True, "desk evidence")
    assert _matrix_present_label(candidate, row) == "marketed_unverified"


def test_desk_zacks_documents_obs_date_and_remains_unverified() -> None:
    candidate = _desk_zacks()

    assert candidate.verdict == "UNVERIFIED"
    assert candidate.probe_status == "desk_review"
    matrix = {row.field: row.present for row in candidate.field_matrix}
    assert matrix["announcement_timestamp_tz"] is True
    assert matrix["estimate_observation_timestamp"] is None
    assert (
        candidate.probe_evidence["primary_tables"]["ZACKS/EEH"]["estimate_observation_field"]
        == "obs_date"
    )
    assert candidate.probe_evidence["table_code_verification"]["pead_pair"] == (
        "ZEEH/ZACKS/EEH + ZES/ZACKS/ES"
    )
    assert "contract amendment" in candidate.probe_evidence["contract_governance"]
    assert any("ZEEH/ZACKS/EEH" in gap for gap in candidate.blocking_gaps)
    assert any("same-day" in gap for gap in candidate.blocking_gaps)


def test_probe_alpha_vantage_marks_estimate_observation_missing() -> None:
    candidate = _probe_alpha_vantage()

    if not candidate.probe_evidence.get("user_key_present"):
        assert candidate.verdict == "UNVERIFIED"
        return

    assert candidate.verdict == "INSUFFICIENT"
    matrix = {row.field: row.present for row in candidate.field_matrix}
    assert matrix["estimate_observation_timestamp"] is False
    assert matrix["announcement_timestamp_tz"] is False
    assert matrix["actual_eps"] is True
    assert matrix["consensus_eps"] is True
    assert any("estimate observation timestamp" in gap for gap in candidate.blocking_gaps)


def test_probe_fmp_marks_estimate_observation_missing() -> None:
    candidate = _probe_fmp()

    if not candidate.probe_evidence.get("user_key_present"):
        assert candidate.verdict == "UNVERIFIED"
        return

    assert candidate.verdict == "INSUFFICIENT"
    matrix = {row.field: row.present for row in candidate.field_matrix}
    assert matrix["estimate_observation_timestamp"] is False
    assert matrix["announcement_timestamp_tz"] is False
    assert any("estimate observation timestamp" in gap for gap in candidate.blocking_gaps)


def test_probe_twelve_data_marks_estimate_observation_missing() -> None:
    candidate = _probe_twelve_data()

    assert candidate.verdict == "INSUFFICIENT"
    matrix = {row.field: row.present for row in candidate.field_matrix}
    assert matrix["estimate_observation_timestamp"] is False
    assert matrix["announcement_timestamp_tz"] is False
    assert matrix["actual_eps"] is True
    assert matrix["consensus_eps"] is True
    assert any("estimate observation timestamp" in gap for gap in candidate.blocking_gaps)


def test_build_manifest_documents_blocker() -> None:
    audit = PeadSourceAudit(
        verdict="BLOCKED",
        local_snapshots_found=0,
        candidates_evaluated=1,
        data_pass_candidates=0,
        unverified_paid_candidates=1,
        leading_blocker="no estimate observation timestamps",
        issues=("no licensed local snapshot",),
    )
    candidate = SourceCandidate(
        name="Example",
        tier=3,
        verdict="INSUFFICIENT",
        cost="free",
        license_summary="test",
        coverage_claim="test",
        probe_status="probed",
        blocking_gaps=("no estimate observation timestamp",),
        field_matrix=(
            FieldMatrixRow("estimate_observation_timestamp", False, "missing"),
        ),
    )
    text = build_manifest(
        audit,
        [candidate],
        command="python -m research.new_edge.pead.data.audit_pead_sources",
        provenance_path=Path("research/new_edge/pead/data/provenance/example.json"),
        prod_status="ok",
        prod_paths=[],
    )

    assert "Verdict: BLOCKED" in text
    assert "estimate_observation_timestamp" in text
    assert "Owner decision required" in text
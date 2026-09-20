"""Cross-language contract tests for the frozen parallel-repair protocol.

P01 freeze: every sample in tests/fixtures/parallel_contracts/wire-fixtures.json must
round-trip through src/morrow/core/contracts.py exactly. A mismatch here means
a lane drifted from the frozen wire protocol; fix the contract first (version
bump by the coordinator), never the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.core.contracts import (
    CONTRACT_VERSION,
    ExecutionSegmentIdentity,
    GuiControlRequest,
    GuiPauseRequest,
    PauseIntentFact,
    PlanningRequestOutcome,
    SafeContentRef,
    TimelineCursor,
    TimelineEntryIdentity,
    TimelineSinkEntry,
    TimelineSnapshotPage,
    TurnInterruptOutcome,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "parallel_contracts"

SAMPLE_MODELS = {
    "turn_interrupt_outcome": TurnInterruptOutcome,
    "pause_intent_fact": PauseIntentFact,
    "execution_segment_identity": ExecutionSegmentIdentity,
    "planning_request_outcome_model_request": PlanningRequestOutcome,
    "planning_request_outcome_validation": PlanningRequestOutcome,
    "planning_request_outcome_operation": PlanningRequestOutcome,
    "timeline_entry_identity": TimelineEntryIdentity,
    "timeline_sink_entry": TimelineSinkEntry,
    "safe_content_ref": SafeContentRef,
    "timeline_cursor": TimelineCursor,
    "timeline_snapshot_page": TimelineSnapshotPage,
    "gui_control_request": GuiControlRequest,
    "gui_pause_request": GuiPauseRequest,
}


def _fixtures() -> dict:
    return json.loads((CONTRACTS_DIR / "wire-fixtures.json").read_text(encoding="utf-8"))


def test_contract_version_matches_wire_fixtures():
    fixtures = _fixtures()
    assert fixtures["contract_version"] == CONTRACT_VERSION


@pytest.mark.parametrize("sample_name", sorted(SAMPLE_MODELS))
def test_wire_sample_round_trips_through_the_frozen_model(sample_name):
    sample = _fixtures()["samples"][sample_name]
    model = SAMPLE_MODELS[sample_name]
    parsed = model.model_validate(sample)
    assert parsed.model_dump(mode="json") == sample


def test_unknown_fields_are_rejected_everywhere():
    sample = dict(_fixtures()["samples"]["timeline_entry_identity"])
    sample["surprise_field"] = "nope"
    with pytest.raises(ValidationError):
        TimelineEntryIdentity.model_validate(sample)


def test_planning_outcome_must_match_its_layer():
    sample = _fixtures()["samples"]["planning_request_outcome_validation"]
    broken = {**sample, "outcome": "succeeded"}
    with pytest.raises(ValidationError):
        PlanningRequestOutcome.model_validate(broken)

"""Stage 7 v1 representation, exact binding semantics and calibrated safety."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morrow.application.workflows.evidence import text_result_from_assistant, workflow_task_outcome
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.domain import (
    TASK_OUTCOME_ARTIFACT_MAX_REFS,
    TaskOutcome,
    TaskOutcomeEvidenceKind,
    TaskOutcomeEvidenceRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TextSafetyProfile,
)
from morrow.core.models import ModelRef
from morrow.core.workflows.contracts import (
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    TextResult,
)
from morrow.core.workflows.definitions import (
    AgentNode,
    AgentNodeSource,
    CompiledWorkflow,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
    WorkflowRevision,
    compiled_content_hash,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
BUDGET = WorkflowBudget(
    max_agent_generation_requests=10,
    default_node_max_agent_generation_requests=3,
    admission_timeout_seconds=300,
    max_concurrency=1,
)
REF = AgentDefinitionRef(definition_id="helper", version_id="adev_one", content_hash="a" * 64)


def node(node_id="worker", **changes):
    return AgentNode(
        **{
            "node_id": node_id,
            "agent_definition_ref": REF,
            "task_contract": TaskContract(objective="Inspect authorization tests"),
            "output_contracts": (OutputContract(slot="result"),),
            "access_mode": "read",
            "resolved_model_ref": ModelRef(provider_id="fake", model_id="m1"),
            "declared_node_max_agent_generation_requests": 3,
            **changes,
        }
    )


def candidate(**changes):
    return CompiledWorkflow(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Authorization audit",
            "nodes": (node(),),
            "required_outputs": (NodeOutputRef(node_id="worker", output_slot="result"),),
            "entry_nodes": ("worker",),
            "terminal_nodes": ("worker",),
            "budget": BUDGET,
            "compiler_version": "stage7-v1",
            **changes,
        }
    )


def revision(**changes):
    value = candidate(**changes)
    return WorkflowRevision(
        **value.model_dump(),
        workflow_revision_id="wrev_one",
        workspace_id="ws_one",
        revision=1,
        content_hash=compiled_content_hash(value),
        source_revision=0,
        source_hash=source().content_hash,
        created_by="cmd_publish",
        created_at=NOW,
    )


def source(**changes):
    value = candidate()
    data = value.model_dump(
        exclude={"nodes", "entry_nodes", "terminal_nodes", "budget", "compiler_version"}
    )
    return WorkflowDefinitionSource(
        **{
            **data,
            "default_budget": BUDGET,
            "nodes": (
                AgentNodeSource(
                    **node().model_dump(
                        exclude={
                            "resolved_model_ref",
                            "resolved_tool_requirements",
                            "declared_node_max_agent_generation_requests",
                        }
                    )
                ),
            ),
            **changes,
        }
    )


def outcome_fields():
    return dict(
        outcome_id="out_one",
        workspace_id="ws_one",
        session_id="ses_root",
        task_run_id="task_root",
        version=1,
        trigger=TaskOutcomeTrigger.SNAPSHOT,
        task_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
        summary="Authorization test passed",
        created_at=NOW,
    )


def test_canonical_revision_separates_source_and_immutable_identity():
    first = revision()
    assert WorkflowRevision.model_validate_json(first.model_dump_json()) == first
    assert (
        compiled_content_hash(
            first.model_copy(update={"workflow_revision_id": "wrev_two", "revision": 2})
        )
        == first.content_hash
    )
    assert compiled_content_hash(candidate(description="changed")) != first.content_hash
    for changes in (
        {"enabled": True},
        {"entry_nodes": ["worker"]},
        {"text_safety_profile": "workflow_value_sensitive"},
    ):
        with pytest.raises(ValidationError):
            source(**changes)
    with pytest.raises(ValidationError, match="hash"):
        WorkflowRevision.model_validate({**first.model_dump(), "content_hash": "f" * 64})
    assert node(conversation_scope="invoking_session").conversation_scope == "invoking_session"
    assert node(access_mode="write").access_mode == "write"


def test_output_binding_export_and_observation_are_separate_facts():
    first = node(
        "explore",
        output_contracts=(
            OutputContract(slot="bound"),
            OutputContract(slot="observe", required_for_node_completion=False),
        ),
    )
    second = node(
        input_bindings=(
            NodeOutputBinding(
                source="node_output",
                input_name="context",
                accepts={"kind": "TextResult"},
                node_output=NodeOutputRef(node_id="explore", output_slot="bound"),
            ),
        )
    )
    valid = dict(
        nodes=(first, second),
        edges=(WorkflowEdge(from_node_id="explore", to_node_id="worker"),),
        entry_nodes=("explore",),
    )
    assert candidate(**valid).required_outputs[0].node_id == "worker"
    with pytest.raises(ValidationError, match="completion-required"):
        candidate(
            **valid, required_outputs=(NodeOutputRef(node_id="explore", output_slot="observe"),)
        )
    with pytest.raises(ValidationError, match="same-direction"):
        candidate(
            nodes=(first, second),
            entry_nodes=("explore", "worker"),
            terminal_nodes=("explore", "worker"),
        )
    # Pure control dependency is legal without an Artifact binding.
    assert candidate(**{**valid, "nodes": (first, node())})
    with pytest.raises(ValidationError, match="unique"):
        node(output_contracts=(OutputContract(slot="result"), OutputContract(slot="result")))
    raw = source().model_dump()
    raw["nodes"][0]["input_bindings"] = [
        {
            "source": "workflow_input",
            "input_name": "task",
            "accepts": {"kind": "TaskContract"},
            "workflow_input": "task",
            "node_output": {"node_id": "x", "output_slot": "y"},
        }
    ]
    with pytest.raises(ValidationError):
        WorkflowDefinitionSource.model_validate(raw)


def test_shared_export_bound_and_optional_execution_limits():
    refs = tuple(
        NodeOutputRef(node_id="worker", output_slot=f"slot_{i}")
        for i in range(TASK_OUTCOME_ARTIFACT_MAX_REFS)
    )
    assert candidate(
        nodes=(node(output_contracts=tuple(OutputContract(slot=r.output_slot) for r in refs)),),
        required_outputs=refs,
    )
    with pytest.raises(ValidationError):
        candidate(required_outputs=(*refs, NodeOutputRef(node_id="worker", output_slot="overflow")))
    for value in (0, float("inf"), float("nan"), True, "300"):
        with pytest.raises(ValidationError):
            WorkflowBudget(**{**BUDGET.model_dump(), "admission_timeout_seconds": value})
    assert WorkflowBudget().model_dump() == {
        "max_agent_generation_requests": None,
        "default_node_max_agent_generation_requests": None,
        "admission_timeout_seconds": None,
        "max_concurrency": 1,
    }
    for kind in ("TextResult", "ReviewReport"):
        with pytest.raises(ValidationError):
            source(input_contract={"kind": kind, "version": 1})


@pytest.mark.parametrize(
    "text",
    [
        "password_validation.py",
        "authorization test passed",
        "401 authorization failed",
        "credential rotation tests",
        "api_key is a code identifier",
        "password = placeholder",
        "password = <redacted>",
        "password = $PASSWORD",
        "api_key: your_api_key",
    ],
)
def test_workflow_safety_benign_calibration(text):
    assert TaskContract(objective=text).objective == text
    result = text_result_from_assistant("rec_final", text)
    assert result.excerpt == text
    # Placeholders remain legal and visible. An existing redaction marker cannot establish
    # complete source evidence, even when this layer did not perform the earlier redaction.
    assert result.content_complete is ("<redacted>" not in text)
    assert TextResult.model_validate_json(result.model_dump_json()) == result
    outcome = workflow_task_outcome(**{**outcome_fields(), "summary": text})
    assert TaskOutcome.model_validate_json(outcome.model_dump_json()) == outcome
    assert outcome.text_safety_profile == TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE


@pytest.mark.parametrize(
    "text",
    [
        "sk-" + "x" * 24,
        "password = actual-test-value",
        "api_key: non-placeholder-test",
        "Bearer " + "z" * 24,
        "ghp_" + "y" * 24,
    ],
)
def test_workflow_safety_actual_value_calibration(text):
    with pytest.raises(ValidationError):
        TaskContract(objective=text)
    result = text_result_from_assistant("rec_final", "before " + text + " after")
    assert text not in result.excerpt and not result.content_complete
    assert TextResult.model_validate_json(result.model_dump_json()) == result
    outcome = workflow_task_outcome(
        **{**outcome_fields(), "summary": text, "validation_facts": (text,)}
    )
    assert text not in outcome.model_dump_json()
    assert "workflow_evidence_redacted=true" in outcome.completion_basis
    assert TaskOutcome.model_validate_json(outcome.model_dump_json()) == outcome


def test_legacy_default_and_typed_workflow_evidence():
    with pytest.raises(ValidationError):
        TaskOutcome(**outcome_fields())
    evidence = TaskOutcomeEvidenceRef(
        kind=TaskOutcomeEvidenceKind.WORKFLOW_RUN,
        reference_id="wrun_one",
        role="workflow_result_snapshot",
    )
    assert evidence.reference_id == "wrun_one"
    with pytest.raises(ValidationError):
        TaskOutcomeEvidenceRef(
            kind=TaskOutcomeEvidenceKind.TURN,
            reference_id="turn_one",
            role="workflow_result_snapshot",
        )
    with pytest.raises(ValidationError):
        TaskOutcomeEvidenceRef(kind=TaskOutcomeEvidenceKind.WORKFLOW_RUN, reference_id="turn_one")


def test_profile_aware_diagnostic_envelopes_preserve_legacy_rejection():
    from morrow.core.diagnostics import PublicDiagnosticError
    from morrow.core.execution import HandlerResultEnvelope
    from morrow.core.recovery import RecoveryReport

    fields = dict(ok=False, error_code="command_failed", error_message="401 authorization failed")
    with pytest.raises(ValidationError):
        HandlerResultEnvelope(**fields)
    value = HandlerResultEnvelope(
        **fields, text_safety_profile=TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
    )
    assert HandlerResultEnvelope.model_validate_json(value.model_dump_json()) == value
    assert (
        PublicDiagnosticError(
            "command_failed",
            fields["error_message"],
            text_safety_profile=TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE,
        ).message
        == fields["error_message"]
    )
    with pytest.raises(ValueError):
        PublicDiagnosticError("command_failed", fields["error_message"])
    report = RecoveryReport(
        report_id="rrp_one",
        workspace_id="ws_one",
        session_id="ses_root",
        text_safety_profile=TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE,
    )
    assert RecoveryReport.model_validate_json(report.model_dump_json()) == report


def test_workflow_input_discriminator_and_observation_binding_rejection():
    from morrow.core.workflows.contracts import WorkflowInputBinding

    binding = WorkflowInputBinding(
        source="workflow_input",
        input_name="goal",
        accepts={"kind": "TaskContract"},
        workflow_input="task",
    )
    assert node(input_bindings=(binding,)).input_bindings == (binding,)
    with pytest.raises(ValidationError):
        WorkflowInputBinding.model_validate({**binding.model_dump(), "workflow_input": "other"})
    observation = node(
        "inspect",
        output_contracts=(OutputContract(slot="result", required_for_node_completion=False),),
    )
    downstream = node(
        input_bindings=(
            NodeOutputBinding(
                source="node_output",
                input_name="observation",
                accepts={"kind": "TextResult"},
                node_output={"node_id": "inspect", "output_slot": "result"},
            ),
        )
    )
    with pytest.raises(ValidationError, match="completion-required"):
        candidate(
            nodes=(observation, downstream),
            edges=(WorkflowEdge(from_node_id="inspect", to_node_id="worker"),),
            entry_nodes=("inspect",),
        )

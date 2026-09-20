"""Completion-protocol regressions for the workflow leaf submit tool.

Covers the TextResult incident: a node whose only output contract is a
TextResult must complete through the final assistant reply, its submit tool
must say so explicitly, and an empty submission must answer with the stable
``empty_submission`` correction instead of an opaque value_error.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from morrow.application.workflows.submit import make_submit_node_result_tool
from morrow.core.workflows.contracts import OutputContract
from morrow.runtime.tool_arguments import ToolArgumentsValidationError
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.testing import make_run_policy

TEXT_RESULT_CONTRACT = OutputContract(kind="TextResult", slot="result")

VALID_REPLAN = {
    "schema_version": 2,
    "reason": "missing_dependency",
    "affected_facts": [{"summary": "缺少 utils 模块", "impact": "invalidate"}],
}


class RecordingHooks:
    def __init__(self) -> None:
        self.arguments = None
        self.call_id = None

    def submit_node_result(self, arguments, *, call_id=None):
        self.arguments = arguments
        self.call_id = call_id
        return {"submitted": True}


def _tool(contract, hooks=None):
    return make_submit_node_result_tool(hooks or RecordingHooks(), (contract,))


def _executor(tool) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry.snapshot(), make_run_policy())


def _call(name: str, arguments: dict):
    return SimpleNamespace(
        id="call_1", name=name, arguments=json.dumps(arguments, ensure_ascii=False)
    )


def test_text_result_node_switches_to_the_replan_only_protocol():
    tool = _tool(TEXT_RESULT_CONTRACT)
    schema = tool.definition.function.parameters

    # The declared-but-empty outputs slot keeps the curated rejection on the
    # pydantic pass; a JSON required entry would only answer with a generic
    # missing-field error.
    assert schema["properties"]["outputs"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "minProperties": 0,
    }
    assert "replan" not in schema.get("required", [])
    description = tool.definition.function.description
    assert "NO structured output slots" in description
    assert "final report" in description
    # The current protocol lets such a node register delivered files.
    assert "deliverables" in description
    assert schema["properties"]["deliverables"]["type"] == "object"


#: Frozen fingerprints of the v1 submit tool schema, captured before the v2
#: protocol was introduced. A stored AgentRun rebuilds its tool schema from
#: (node output contracts, revision protocol version); if these change, old
#: runs can no longer recover and the drift is a compatibility break.
V1_TOOL_SCHEMA_DIGESTS = {
    "text_result": "60d23ff3ae6ca7a1b171773e9db3d58edcf2ee830588e269a59ee4813f673dd5",
    "synthesis": "96f3f698825f006b0c5f4aa25f77ff90fc5882d72bb2687679bc2ccbf589d8ed",
    "mixed": "749d01988e183e1686579b7507952ab42db6a619ee119cdc90f2c2e540b53bf6",
    "optional_plan": "8c4370f49b7f5f8716aa1bd25b074f0b9c346ae54ae2f3fe2ed5b71972bb633a",
}
V1_TOOL_SCHEMA_CONTRACTS = {
    "text_result": (TEXT_RESULT_CONTRACT,),
    "synthesis": (OutputContract(kind="SynthesisReport", slot="report"),),
    "mixed": (
        OutputContract(kind="PlanArtifact", slot="plan"),
        TEXT_RESULT_CONTRACT,
    ),
    "optional_plan": (
        OutputContract(kind="PlanArtifact", slot="plan", required_for_node_completion=False),
    ),
}


@pytest.mark.parametrize("name", sorted(V1_TOOL_SCHEMA_DIGESTS))
def test_v1_submit_tool_schema_stays_frozen(name):
    from morrow.application.agent_runs.preparation import tool_schema_digest
    from morrow.core.workflows.contracts import SUBMIT_SCHEMA_V1

    tool = make_submit_node_result_tool(
        RecordingHooks(),
        V1_TOOL_SCHEMA_CONTRACTS[name],
        protocol_version=SUBMIT_SCHEMA_V1,
    )
    schema = tool.definition.function.parameters
    assert "deliverables" not in schema["properties"]
    assert tool_schema_digest((tool.definition,)) == V1_TOOL_SCHEMA_DIGESTS[name]


def test_current_protocol_advertises_registration_and_keeps_the_v1_flow():
    from morrow.application.agent_runs.preparation import tool_schema_digest
    from morrow.core.workflows.contracts import SUBMIT_SCHEMA_V1

    current = _tool(OutputContract(kind="SynthesisReport", slot="report"))
    frozen = make_submit_node_result_tool(
        RecordingHooks(),
        (OutputContract(kind="SynthesisReport", slot="report"),),
        protocol_version=SUBMIT_SCHEMA_V1,
    )
    assert tool_schema_digest((current.definition,)) != tool_schema_digest((frozen.definition,))
    assert current.definition.function.parameters["properties"]["deliverables"]["type"]


def test_empty_submission_names_the_completion_protocol():
    tool = _tool(TEXT_RESULT_CONTRACT)
    with pytest.raises(ToolArgumentsValidationError) as exc:
        tool.arguments_validator.validate(json.dumps({"outputs": {}, "summary": "调查完成"}))

    assert exc.value.code == "empty_submission"
    assert "最终答复" in str(exc.value)
    assert exc.value.details == ({"path": "$", "type": "empty_submission"},)


def test_structured_slot_on_a_text_result_node_is_rejected_at_the_schema():
    tool = _tool(TEXT_RESULT_CONTRACT)
    with pytest.raises(ToolArgumentsValidationError) as exc:
        tool.arguments_validator.validate(json.dumps({"outputs": {"result": {"text": "x"}}}))

    assert exc.value.details == ({"path": "outputs.result", "type": "additionalProperties"},)


def test_structured_node_keeps_its_slot_requirements():
    tool = _tool(OutputContract(kind="PlanArtifact", slot="plan"))
    with pytest.raises(ToolArgumentsValidationError) as exc:
        tool.arguments_validator.validate(json.dumps({"outputs": {}}))

    assert exc.value.details == ({"path": "outputs.plan", "type": "missing"},)


def test_mixed_node_description_excludes_text_result_from_submission():
    tool = _tool(
        OutputContract(kind="PlanArtifact", slot="plan"),
    )
    plain = tool.definition.function.description
    assert "TextResult outputs are never submitted here" not in plain

    mixed = make_submit_node_result_tool(
        RecordingHooks(),
        (OutputContract(kind="PlanArtifact", slot="plan"), TEXT_RESULT_CONTRACT),
    )
    assert "TextResult outputs are never submitted here" in mixed.definition.function.description


def test_replan_only_submission_reaches_the_hook_with_replan_intact():
    hooks = RecordingHooks()
    executor = _executor(_tool(TEXT_RESULT_CONTRACT, hooks))

    outcome = asyncio.run(
        executor.execute(_call("submit_node_result", {"outputs": {}, "replan": VALID_REPLAN}))
    )

    assert outcome.ok
    assert json.loads(outcome.envelope)["result"] == {"submitted": True}
    assert hooks.arguments is not None
    assert hooks.arguments.outputs == {}
    assert hooks.arguments.replan is not None
    assert hooks.arguments.replan.reason == "missing_dependency"


def test_empty_submission_outcome_carries_the_stable_reason():
    executor = _executor(_tool(TEXT_RESULT_CONTRACT))

    outcome = asyncio.run(
        executor.execute(_call("submit_node_result", {"outputs": {}, "summary": "ok"}))
    )

    assert not outcome.ok
    assert outcome.validation_reason == "empty_submission"
    assert outcome.validation_path == "$"
    error = json.loads(outcome.envelope)["error"]
    assert error["reason"] == "empty_submission"
    assert "最终答复" in error["message"]
    assert error["details"] == [{"path": "$", "type": "empty_submission"}]


def test_v1_submission_digest_keeps_its_frozen_input_shape():
    """A v1 replay must recompute exactly the digest its marker recorded."""

    from morrow.application.workflows.submit import submission_digest
    from morrow.core.domain import canonical_json_bytes, sha256_digest
    from morrow.core.workflows.contracts import PlanArtifact

    payload = PlanArtifact(steps=("do it",))
    expected = sha256_digest(
        canonical_json_bytes(
            {
                "outputs": {"plan": payload.model_dump(mode="json")},
                "summary": "submitted",
                "evidence_refs": ["tex_1"],
            }
        )
    )
    assert submission_digest({"plan": payload}, "submitted", ("tex_1",)) == expected
    assert (
        submission_digest(
            {"plan": payload},
            "submitted",
            ("tex_1",),
            deliverables={"plan": [{"path": "a.py", "label": None}]},
        )
        != expected
    )

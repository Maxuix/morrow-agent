"""Planning pause lifecycle, layered request outcomes and durable resume (P05).

Domain tests for lane B. They exercise the real server composition
(ServerFixture) plus a stubbed plan generator for deterministic waits; the
pause/resume HTTP routes and GUI projection are wired by the coordinator and
stay listed as pending integration in the lane report.
"""

import asyncio
import json

import pytest

from morrow.core.application import ApplicationError
from morrow.core.workflows.planning import (
    PausePlanningGenerationRequest,
    PlanSpec,
    PlanWorkflowRequest,
    ResumePlanningGenerationRequest,
)
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture
from test_workflow_task_planning import request, spec


class HangingGenerator:
    """Stub plan generator that blocks until released (no wall-clock sleeps)."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, prepared, *, diagnostics, observe):
        self.entered.set()
        await self.release.wait()
        return PlanSpec.model_validate(spec("fresh"))


def _restore_real_generator(svc):
    from morrow.application.workflows.planning_model import ModelPlanGenerator

    svc.generator = ModelPlanGenerator(
        svc.context.application.provider_service, svc.context.api.artifacts
    )


def pause_request(sid, operation_id, command_id="cmd_pause", **changes):
    return PausePlanningGenerationRequest.model_validate(
        {
            "command_id": command_id,
            "session_id": sid,
            "planning_operation_id": operation_id,
            **changes,
        }
    )


def resume_request(sid, operation_id, command_id="cmd_resume", **changes):
    return ResumePlanningGenerationRequest.model_validate(
        {
            "command_id": command_id,
            "session_id": sid,
            "planning_operation_id": operation_id,
            **changes,
        }
    )


async def facts(fx, sid, identity=None):
    def read():
        svc = fx.host.context.chat.planning
        op_identity = identity
        if op_identity is None:
            ops = svc.repo.operations(svc.workspace_id, sid)
            op_identity = ops[-1].planning_operation_id
        return {
            "operation_id": op_identity,
            "operation": svc.repo.operation(svc.workspace_id, sid, op_identity),
            "pause_facts": svc.repo.pause_facts(svc.workspace_id, op_identity),
            "outcomes": svc.repo.outcomes(svc.workspace_id, op_identity),
            "paused": svc.repo.paused(svc.workspace_id, op_identity),
            "view": svc.view(sid),
            "requests": fx.host.context.journal._backend.read_all(
                "SELECT attempt,body_json FROM workflow_planning_requests WHERE planning_operation_id=?",
                (op_identity,),
            ),
        }

    return await fx.on_core(read)


async def test_first_planning_input_persists_title_and_request_payload(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, root = await new_session(fx)
        reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_in"))
        assert reply.status == 200, reply.body
        state = await facts(fx, sid)

        def title_and_payload():
            svc = fx.host.context.chat.planning

            metadata = svc.journal.session_metadata.get(svc.workspace_id, sid)
            payload = PlanWorkflowRequest.model_validate_json(
                state["operation"].accepted_request_json
            )
            return (
                metadata["title"],
                payload.digest == state["operation"].request_digest,
                payload.task.objective,
            )

        title, digest_ok, objective = await fx.on_core(title_and_payload)
        # The accepted goal named the Session in the admission transaction.
        assert "Implement the requested calculator" in title
        assert digest_ok and "Implement the requested calculator" == objective

        # A user-defined title wins over every later planning input.
        def customize():
            svc = fx.host.context.chat.planning
            current = svc.journal.session_metadata.get(svc.workspace_id, sid)

            def work(_):
                svc.journal.session_metadata.update(
                    svc.workspace_id,
                    sid,
                    expected_revision=current["revision"],
                    title="自定义标题",
                )

            svc.journal.transact(work)

        await fx.on_core(customize)
        replay = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_in"))
        assert replay.status == 200
        state2 = await facts(fx, sid)

        def read_title():
            svc = fx.host.context.chat.planning
            return svc.journal.session_metadata.get(svc.workspace_id, sid)["title"]

        assert await fx.on_core(read_title) == "自定义标题"
        assert state2["operation"].planning_operation_id == state["operation"].planning_operation_id
    finally:
        fx.close()


async def test_pause_resume_lifecycle_facts_projection_and_request_sequence(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            generator = HangingGenerator()
            svc.generator = generator
            asyncio.create_task(svc.dispatch(prepared, command=fx.host.execute_command))
            await generator.entered.wait()
            svc.pause(pause_request(sid, identity, command_id="cmd_pause"))
            with pytest.raises(asyncio.CancelledError):
                await svc.jobs[identity]
            return identity

        identity = await fx.host.execute_preparation(journey)
        state = await facts(fx, sid)
        assert state["operation"].planning_operation_id == identity
        # The suspended safe point is durable and projected as paused.
        assert state["paused"] is True
        lifecycles = [row[3] for row in state["pause_facts"]]
        assert lifecycles == ["requested", "quiescing", "suspended"]
        assert state["view"]["operations"][0]["effective_status"] == "paused"
        assert "resume_generation" in state["view"]["allowed_actions"]
        # The interactive generation handle binds the paused operation for the UI.
        assert state["view"]["generation"]["planning_operation_id"] == identity
        assert state["view"]["generation"]["status"] == "paused"
        interrupted = [
            row
            for row in state["outcomes"]
            if row[0] == "model_request" and row[3] == "interrupted"
        ]
        assert [(row[1], row[2], row[6]) for row in interrupted] == [(1, 1, "user_pause")]

        async def finish():
            svc = fx.host.context.chat.planning
            _restore_real_generator(svc)
            resumed = svc.resume(resume_request(sid, identity))
            assert resumed.prepared is not None
            result = await svc.dispatch(resumed.prepared, command=fx.host.execute_command)
            return result.status

        assert await fx.host.execute_preparation(finish) == "succeeded"
        state = await facts(fx, sid)
        # Resume kept the attempt (validation budget untouched) and used a new
        # request sequence for the real retry call (P05.3).
        model_rows = [
            (row[1], row[2], row[3]) for row in state["outcomes"] if row[0] == "model_request"
        ]
        assert model_rows == [(1, 1, "interrupted"), (1, 2, "succeeded")]
        assert state["view"]["operations"][0]["effective_status"] == "succeeded"
        assert state["view"]["generation"] is None  # terminal: no open handle
        assert "pause_generation" not in state["view"]["allowed_actions"]

        # Planning never auto-starts the plan (D16).
        def run_count():
            return fx.host.context.journal._backend.read_one(
                "SELECT count(*) FROM workflow_runs", ()
            )[0]

        assert await fx.on_core(run_count) == 0
    finally:
        fx.close()


async def test_pause_restart_resume_rebuilds_from_durable_input(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            generator = HangingGenerator()
            svc.generator = generator
            asyncio.create_task(svc.dispatch(prepared, command=fx.host.execute_command))
            await generator.entered.wait()
            svc.pause(pause_request(sid, identity))
            return identity

        identity = await fx.host.execute_preparation(journey)
    finally:
        fx.close()

    # A fresh process on the same data root: startup recovery must keep the
    # paused operation resumable instead of marking it failed (A09).
    fx2 = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        state = await facts(fx2, sid)
        assert state["paused"] is True
        assert state["operation"].status == "running"
        assert state["operation"].accepted_request_json is not None

        async def finish():
            svc = fx2.host.context.chat.planning
            _restore_real_generator(svc)
            resumed = svc.resume(resume_request(sid, identity))
            assert resumed.prepared is not None
            result = await svc.dispatch(resumed.prepared, command=fx2.host.execute_command)
            view = svc.view(sid)
            return result.status, view["operations"][0]["usage"]["attempts"]

        status, attempts = await fx2.host.execute_preparation(finish)
        assert status == "succeeded"
        assert attempts == 2  # interrupted request + resumed request
        state = await facts(fx2, sid)
        assert state["view"]["draft"] is not None
    finally:
        fx2.close()


async def test_graceful_shutdown_persists_resumable_interrupt(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            generator = HangingGenerator()
            svc.generator = generator
            asyncio.create_task(svc.dispatch(prepared, command=fx.host.execute_command))
            await generator.entered.wait()
            await svc.shutdown()
            return identity

        identity = await fx.host.execute_preparation(journey)
        state = await facts(fx, sid)
        kinds = {row[2] for row in state["pause_facts"]}
        assert kinds == {"shutdown"}
        assert state["paused"] is True
        assert state["operation"].status == "running"  # not a user cancel

        async def finish():
            svc = fx.host.context.chat.planning
            _restore_real_generator(svc)
            resumed = svc.resume(resume_request(sid, identity))
            return await svc.dispatch(resumed.prepared, command=fx.host.execute_command)

        assert (await fx.host.execute_preparation(finish)).status == "succeeded"
    finally:
        fx.close()


async def test_user_cancel_stays_distinct_from_pause(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            generator = HangingGenerator()
            svc.generator = generator
            asyncio.create_task(svc.dispatch(prepared, command=fx.host.execute_command))
            await generator.entered.wait()
            # Cooperative cancel: the terminal fact lands while the model wait
            # is still open (the execution projection keeps showing planning).
            svc.cancel(sid, identity)
            assert svc.get_operation(sid, identity).status == "cancelled"
            # The generation task observes the terminal state when the wait
            # resolves and settles its own request evidence honestly.
            generator.release.set()
            result = await svc.jobs[identity]
            return identity, result.status

        identity, job_status = await fx.host.execute_preparation(journey)
        assert job_status == "cancelled"
        state = await facts(fx, sid)
        assert state["operation"].status == "cancelled"
        assert state["pause_facts"] == ()
        # The request that really completed is recorded as succeeded (never
        # overwritten into a fake pause outcome); no resumable safe point.
        model_rows = [
            (row[1], row[2], row[3]) for row in state["outcomes"] if row[0] == "model_request"
        ]
        assert model_rows == [(1, 1, "succeeded")]

        async def refuse():
            svc = fx.host.context.chat.planning
            svc.resume(resume_request(sid, identity))

        with pytest.raises(ApplicationError):
            await fx.host.execute_command(refuse)
    finally:
        fx.close()


async def test_pause_replay_is_idempotent_and_payload_conflict_detected(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            first = svc.pause(pause_request(sid, identity, command_id="cmd_pause"))
            replay = svc.pause(pause_request(sid, identity, command_id="cmd_pause"))
            assert replay.planning_operation_id == first.planning_operation_id
            facts_count = len(svc.repo.pause_facts(svc.workspace_id, identity))
            with pytest.raises(ApplicationError, match="reused"):
                svc.pause(
                    pause_request(sid, identity, command_id="cmd_pause", expected_row_version=99)
                )
            return identity, facts_count

        identity, facts_count = await fx.host.execute_preparation(journey)
        state = await facts(fx, sid)
        assert len(state["pause_facts"]) == facts_count
        assert state["paused"] is True  # replay never ran a second pause
    finally:
        fx.close()


async def test_valid_candidate_saved_while_pause_defers_application(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, _ = await new_session(fx)

        class FakeJob:
            def cancel(self):
                self.cancelled = True

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            # Pause intent accepted while a finished candidate is in hand.
            svc.jobs[identity] = FakeJob()
            svc.pause(pause_request(sid, identity))
            # The candidate arrived from a real model request (sequence 1).
            svc.request_started(identity, 0, prepared.context.model, sid, 1)
            from morrow.application.workflows.task_planning import _DeferredPause

            result = svc.complete(prepared, PlanSpec.model_validate(spec("saved")))
            assert isinstance(result, _DeferredPause)
            svc.jobs.pop(identity)  # the fake job must not reach host shutdown
            svc._suspend_from_loop(svc.repo.operation(svc.workspace_id, sid, identity), prepared)
            return identity

        identity = await fx.host.execute_preparation(journey)
        state = await facts(fx, sid)
        assert state["paused"] is True
        assert state["operation"].result_draft_id is None  # not applied while paused
        assert state["view"]["draft"] is None

        async def finish():
            svc = fx.host.context.chat.planning
            resumed = svc.resume(resume_request(sid, identity))
            # Resume applies the saved candidate: no new generation spend.
            assert resumed.prepared is None
            return resumed.operation.status

        assert await fx.host.execute_preparation(finish) == "succeeded"
        state = await facts(fx, sid)
        assert state["operation"].result_draft_version == 1
        assert len(fx.bank.providers) == 1  # only the composition dummy
    finally:
        fx.close()


async def test_resume_expires_when_base_version_moved(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, root = await new_session(fx)
        first = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_in"))
        assert first.json()["status"] == "succeeded"

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(
                PlanWorkflowRequest.model_validate(
                    request(sid, command_id="cmd_revise", operation="revise", base_draft_version=1)
                )
            )
            identity = prepared.operation.planning_operation_id
            svc.pause(pause_request(sid, identity))  # nothing in flight: suspends now
            # The base draft moves while generation is paused.
            binding = svc.repo.binding(svc.workspace_id, sid)
            current = svc.view(sid)["draft"].draft
            svc.edit(
                sid,
                binding.planning_binding_id,
                source=current.source.model_copy(update={"name": "User moved base"}),
                metadata={},
                expected_version=1,
                command_id="cmd_manual_edit",
            )
            return identity

        identity = await fx.host.execute_preparation(journey)

        async def refuse():
            svc = fx.host.context.chat.planning
            return svc.resume(resume_request(sid, identity)).operation.status

        assert await fx.host.execute_command(refuse) == "expired"
        state = await facts(fx, sid, identity)
        assert state["operation"].status == "expired"
        assert state["operation"].error_code == "stale"
        # No new generation spend after the base moved.
        model_rows = [row for row in state["outcomes"] if row[0] == "model_request"]
        assert model_rows == []
        assert state["view"]["draft"].draft.source.name == "User moved base"
    finally:
        fx.close()


async def test_resume_refuses_accurately_when_budget_or_model_gone(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)

        async def journey():
            svc = fx.host.context.chat.planning
            prepared = svc.begin(PlanWorkflowRequest.model_validate(request(sid)))
            identity = prepared.operation.planning_operation_id
            svc.pause(pause_request(sid, identity))
            from datetime import UTC, datetime

            now = datetime.now(UTC).isoformat()

            def seed(_):
                for display in (1, 2, 3):
                    svc.repo.save_outcome(
                        svc.workspace_id,
                        identity,
                        layer="model_request",
                        attempt=display,
                        request_sequence=display,
                        outcome="succeeded",
                        started_at=now,
                        ended_at=now,
                    )
                    svc.repo.save_outcome(
                        svc.workspace_id,
                        identity,
                        layer="candidate_validation",
                        attempt=display,
                        request_sequence=display,
                        outcome="invalid",
                        reason="invalid_plan",
                        started_at=now,
                        ended_at=now,
                    )

            svc.journal.transact(seed)
            return identity

        identity = await fx.host.execute_preparation(journey)

        async def refuse_budget():
            svc = fx.host.context.chat.planning
            svc.resume(resume_request(sid, identity))

        with pytest.raises(ApplicationError, match="budget"):
            await fx.host.execute_command(refuse_budget)
        state = await facts(fx, sid)
        assert state["paused"] is True  # refusal keeps the paused state intact

        async def refuse_model():
            svc = fx.host.context.chat.planning
            svc.repo.backend.transact(
                lambda: svc.repo.backend.executor().execute(
                    "DELETE FROM workflow_planning_request_outcomes WHERE workspace_id=?",
                    (svc.workspace_id,),
                )
            )
            svc._capabilities = lambda model: (_ for _ in ()).throw(RuntimeError("gone"))
            svc.resume(resume_request(sid, identity, command_id="cmd_resume2"))

        with pytest.raises(ApplicationError, match="no longer available"):
            await fx.host.execute_command(refuse_model)
    finally:
        fx.close()


async def test_provider_failure_yields_distinct_layered_outcomes_without_zero_cost(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[RuntimeError("provider down")]])
    try:
        sid, root = await new_session(fx)
        result = await fx.client.post(root + "/task-plan", request(sid))
        assert result.json()["status"] == "failed"
        assert result.json()["error_code"] == "unavailable"
        state = await facts(fx, sid)
        layers = [(row[0], row[3], row[6]) for row in state["outcomes"]]
        # The scripted error event classifies as network: the compound reason
        # keeps the stable client prefix and records the sanitized cause.
        assert layers == [
            ("planning_operation", "running", None),
            ("model_request", "failed", "planning_provider_unavailable:network"),
            ("candidate_validation", "not_produced", "planning_provider_unavailable:network"),
            ("planning_operation", "failed", "unavailable"),
        ]
        model_row = [row for row in state["outcomes"] if row[0] == "model_request"][0]
        usage = json.loads(model_row[8])["usage"]
        # Cost is unknown after a failed call: never fabricated zero (A11).
        assert usage["availability"] == "unavailable"
        assert state["view"]["operations"][0]["usage"]["cost_amount_minor"] is None
        events = (await fx.client.get(root + "/task-plan/events?after=0")).json()
        assert all(item.get("status") != "finished" for item in events)
        statuses = [item["status"] for item in events if item.get("stage") == "model_finished"]
        assert statuses == ["failed"]
    finally:
        fx.close()


async def test_request_outcome_commits_before_operation_terminal(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("build", "audit"))]]])
    try:
        sid, root = await new_session(fx)
        result = await fx.client.post(root + "/task-plan", request(sid))
        assert result.json()["status"] == "succeeded"
        events = (await fx.client.get(root + "/task-plan/events?after=0")).json()
        order = [item["stage"] for item in events if "stage" in item]
        # The request settles durably before the operation terminal commit.
        assert order.index("model_finished") < order.index("operation_terminal")
        terminal_index = order.index("operation_terminal")
        outcomes_after = [
            item for item in events[terminal_index:] if item.get("stage") == "request_outcome"
        ]
        assert all(item["layer"] != "model_request" for item in outcomes_after)
    finally:
        fx.close()

"""Fresh image/identity failures must never erase or replay a native effect."""

from types import SimpleNamespace

import pytest

from morrow.core.computer_use import ActionOutcome, AxElement, ClickAction, ComputerUseContractError
from test_computer_use_action_service import action_ledger, app_ready
from test_computer_use_observer import environment as _observer_environment


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


async def setup(environment, status="completed"):
    action_ledger(environment)
    app, lifecycle, before = await app_ready(environment)
    device = lifecycle.device

    async def execute(admitted, *, authority):
        authority()
        request = admitted.request
        device.actions.append(request)
        device.observation = device.observation.model_copy(
            update={
                "observation_id": "cobs_after",
                "elements": (AxElement(element_ref="celem_after", depth=1, role="axbutton"),),
            }
        )
        return ActionOutcome(status=status, delivery=request.delivery)

    device.execute_one = execute
    return app, device, before


async def call(app, before, visuals):
    return await app.execute_published(
        "tex_action",
        before.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
        visuals=visuals,
    )


@pytest.mark.parametrize("status", ["completed", "unknown"])
async def test_effect_is_followed_by_new_same_target_image_without_replay(environment, status):
    app, device, before = await setup(environment, status)
    prior_reads = len(device.calls)
    result, references = await call(app, before, environment[0])
    assert result.outcome.status == status
    assert result.outcome.postcondition == "not_checked"
    assert result.outcome.before_observation_id == before.observation.observation_id
    assert result.outcome.after_observation_id == "cobs_after"
    assert result.observation.observation_id == "cobs_after"
    assert result.observation.target_ref == before.observation.target_ref
    assert result.observation.elements[0].element_ref == "celem_after"
    assert result.observation_error is None
    assert len(references) == 1 and references[0].tool_execution_id == "tex_action"
    assert result.observation.image.sha256 == references[0].sha256
    assert len(device.actions) == 1 and len(device.calls) == prior_reads + 1
    repeated, images = await call(app, before, environment[0])
    assert repeated.outcome.error_code == "execution_already_used"
    assert images == () and len(device.actions) == 1
    assert len(device.calls) == prior_reads + 1


async def test_not_started_action_does_not_read_or_publish(environment):
    app, device, before = await setup(environment, "not_started")
    prior_reads = len(device.calls)
    result, references = await call(app, before, None)
    assert result.outcome.status == "not_started"
    assert result.observation is None and references == ()
    assert len(device.calls) == prior_reads and len(device.actions) == 1


@pytest.mark.parametrize("failure", ["target_gone", "opaque"])
async def test_fresh_read_failure_preserves_completed_effect(environment, failure):
    app, device, before = await setup(environment)

    async def fail(admitted):
        device.calls.append(admitted.request)
        if failure == "opaque":
            raise RuntimeError("private native traceback")
        raise ComputerUseContractError("target_gone")

    device.observe = fail
    if failure == "opaque":
        with pytest.raises(RuntimeError, match="private native traceback"):
            await call(app, before, None)
        assert len(device.actions) == 1
        return
    result, references = await call(app, before, None)
    assert result.outcome.status == "completed" and result.outcome.error_code is None
    assert result.outcome.after_observation_id is None and result.observation is None
    assert result.observation_error == failure
    assert references == () and len(device.actions) == 1


@pytest.mark.parametrize("opaque", [False, True])
async def test_publication_failure_keeps_semantic_observation_and_effect(environment, opaque):
    app, device, before = await setup(environment)

    def fail(*args, **kwargs):
        if opaque:
            raise RuntimeError("private pixels")
        raise ComputerUseContractError("image_budget")

    if opaque:
        with pytest.raises(RuntimeError, match="private pixels"):
            await call(app, before, SimpleNamespace(publish_observed=fail))
        assert len(device.actions) == 1
        return
    result, references = await call(app, before, SimpleNamespace(publish_observed=fail))
    assert result.outcome.status == "completed"
    assert result.outcome.after_observation_id == "cobs_after"
    assert result.observation.observation_id == "cobs_after" and result.observation.image is None
    assert result.observation_error == "image_budget"
    assert references == () and len(device.actions) == 1


async def test_reused_observation_id_is_not_a_fresh_observation(environment):
    app, device, before = await setup(environment)

    async def reuse(admitted, *, authority):
        device.actions.append(admitted.request)
        return ActionOutcome(status="completed", delivery=admitted.request.delivery)

    device.execute_one = reuse
    result, references = await call(app, before, None)
    assert result.outcome.status == "completed" and result.observation is None
    assert result.observation_error == "stale_observation"
    assert references == () and device.invalidated and len(device.actions) == 1


async def test_operation_budget_after_effect_preserves_completion(environment):
    app, device, before = await setup(environment)
    # discover + before-read + action consumes all three operations.
    app._run._settings = app._run.settings.model_copy(update={"max_operations": 3})
    result, references = await call(app, before, None)
    assert result.outcome.status == "completed"
    assert result.observation_error == "operation_budget"
    assert result.observation is None and references == () and device.invalidated
    assert len(device.actions) == 1


def revoke(environment):
    from test_computer_use_permissions import NOW

    journal = environment[1]
    grant = journal.get_capability_grant("ws_a", environment[-1].grant_id)
    journal.save_capability_grant(
        "ws_a",
        grant.model_copy(update={"revoked_at": NOW, "revocation_reason": "stop", "row_version": 2}),
        expected_row_version=1,
    )


@pytest.mark.parametrize("phase", ["read", "publish"])
async def test_revocation_during_followup_never_discloses_observation(environment, phase):
    app, device, before = await setup(environment)
    visuals = environment[0]
    if phase == "read":
        device.after_read = lambda: revoke(environment)
    else:

        def publish(*args, **kwargs):
            reference = environment[0].publish_observed(*args, **kwargs)
            revoke(environment)
            return reference

        visuals = SimpleNamespace(publish_observed=publish)
    result, references = await call(app, before, visuals)
    assert result.outcome.status == "completed" and result.outcome.error_code is None
    assert result.observation_error == "grant_inactive"
    assert result.observation is None and references == ()
    assert len(device.actions) == 1 and device.invalidated


async def test_semantic_action_followup_never_publishes_image(environment):
    from morrow.core.runtime_policy import ComputerUseMode

    app, device, before = await setup(environment)
    app._settings = app._settings.model_copy(update={"mode": ComputerUseMode.SEMANTIC})
    app._run._settings = app._settings
    result, references = await call(app, before, None)
    assert result.outcome.status == "completed" and result.observation_error is None
    assert result.observation.observation_id == "cobs_after" and result.observation.image is None
    assert device.calls[-1].include_image is False and references == ()


async def test_followup_cancellation_stops_admission_without_repeating_effect(environment):
    import asyncio

    app, device, before = await setup(environment)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending_read(request):
        entered.set()
        await release.wait()

    device.observe = pending_read
    task = asyncio.create_task(call(app, before, None))
    await entered.wait()
    assert len(device.actions) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert device.invalidated and len(device.actions) == 1


@pytest.mark.parametrize("verified", ["passed", "failed"])
async def test_exact_attribute_result_survives_new_partial_observation(environment, verified):
    from morrow.core.computer_use import parse_computer_action

    app, device, before = await setup(environment)
    original = device.execute_one

    async def execute(admitted, *, authority):
        outcome = await original(admitted, authority=authority)
        device.observation = device.observation.model_copy(update={"complete": False})
        return outcome.model_copy(update={"postcondition": verified})

    device.execute_one = execute
    action = parse_computer_action(
        {
            "type": "click",
            "element_ref": "celem_1",
            "postcondition": {
                "type": "attribute_equals",
                "element_ref": "celem_1",
                "attribute": "enabled",
                "value": "true",
            },
        }
    )
    result, _ = await app.execute_published(
        "tex_action",
        before.observation.observation_id,
        action,
        visuals=environment[0],
    )
    assert result.outcome.postcondition == verified
    assert result.observation.complete is False
    assert result.observation.observation_id == "cobs_after"
    assert len(device.actions) == 1

"""Run budgets, one-use observations and actual journal authority for actions."""

import asyncio
from datetime import timedelta

import pytest

from morrow.core.computer_use import ActionOutcome, ClickAction, CoordinateFrame
from morrow.core.execution import ApprovalResolution, EffectClass, ToolExecutionState
from morrow.runtime.durable_log import durable_call_id
from test_computer_use_observer import _application, _service
from test_computer_use_observer import environment as _observer_environment
from test_computer_use_permissions import NOW
from test_stage4_tool_journal import _approval, _execution, _intent


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


async def ready(environment, **settings):
    service, device, clock, authority = _service(environment, **settings)
    await service.discover(authority=authority)
    read = await service.observe(device.target.target_ref, authority=authority, include_image=False)
    device.actions = []

    async def execute(request, *, settings, authority):
        authority()
        device.actions.append(request)
        return ActionOutcome(status="completed", delivery=request.delivery)

    device.execute_one = execute
    return service, device, clock, authority, read


async def test_action_consumes_observation_and_cannot_replay_or_implicitly_reobserve(environment):
    service, device, _, authority, read = await ready(environment)
    action = ClickAction(type="click", element_ref=None, x=1, y=1)
    # A valid coordinate frame alone never proves the image reached publication.
    device.observation = device.observation.model_copy(
        update={
            "frame": CoordinateFrame(
                width=8,
                height=6,
                scale_x=1.0,
                scale_y=1.0,
                crop_width=8,
                crop_height=6,
            )
        }
    )
    read = await service.observe(device.target.target_ref, authority=authority, include_image=False)
    denied = await service.execute_one(read.observation.observation_id, action, authority=authority)
    assert denied.status == "not_started" and denied.error_code == "image_not_published"
    assert device.actions == []
    # Use the actual safe publisher so the service never manufactures an image proof.
    reference = environment[0].publish(
        read.capture or environment[3],
        read.observation,
        tool_execution_id="tex_observe",
        scope=service.scope,
        settings=service.settings,
    )
    from morrow.core.computer_use import ObservationImageRef

    observation = read.observation.model_copy(
        update={
            "image": ObservationImageRef.model_validate(
                reference.model_dump(include=set(ObservationImageRef.model_fields)),
            )
        }
    )
    service.accept_published_observation(observation)
    before = len(device.calls)
    result = await service.execute_one(observation.observation_id, action, authority=authority)
    again = await service.execute_one(observation.observation_id, action, authority=authority)
    assert (
        result.status == "completed" and result.before_observation_id == observation.observation_id
    )
    assert again.status == "not_started" and again.error_code == "stale_observation"
    assert len(device.actions) == 1 and len(device.calls) == before


async def test_concurrent_action_is_busy_and_never_queued(environment):
    service, device, _, authority, read = await ready(environment)
    from morrow.core.computer_use import AxElement

    device.observation = device.observation.model_copy(
        update={"elements": (AxElement(element_ref="celem_1", depth=1, role="axbutton"),)}
    )
    read = await service.observe(device.target.target_ref, authority=authority, include_image=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting(request, *, settings, authority):
        authority()
        device.actions.append(request)
        entered.set()
        await release.wait()
        return ActionOutcome(status="completed", delivery=request.delivery)

    device.execute_one = waiting
    action = ClickAction(type="click", element_ref="celem_1")
    first = asyncio.create_task(
        service.execute_one(read.observation.observation_id, action, authority=authority)
    )
    await entered.wait()
    second = await service.execute_one(read.observation.observation_id, action, authority=authority)
    assert second.status == "not_started" and second.error_code == "desktop_busy"
    assert len(device.actions) == 1
    release.set()
    assert (await first).status == "completed"


def action_ledger(environment, *, approved=True):
    _, journal, _, _, _, original = environment
    intent = _intent(
        tool_name="computer_action",
        call_id=durable_call_id("action_call"),
        ordinal=3,
        effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        requires_approval=True,
    )
    execution = _execution(
        intent=intent,
        tool_execution_id="tex_action",
        state=ToolExecutionState.EXECUTING,
        permission_snapshot_id=original.permission_snapshot_id,
        grant_id=original.grant_id,
        isolation=original.isolation,
    )
    journal.put_execution("ws_a", execution)
    if approved:
        journal.put_approval(
            "ws_a",
            _approval(
                intent,
                tool_execution_id="tex_action",
                requested_scope="computer_action",
                granted_scope="computer_action",
                resolution=ApprovalResolution.APPROVED,
                resolved_at=NOW,
                consumed_at=NOW,
                permission_snapshot_id=execution.permission_snapshot_id,
                grant_id=execution.grant_id,
                isolation=execution.isolation,
            ),
        )


async def app_ready(environment):
    application, lifecycle = _application(environment)
    from morrow.core.computer_use import AxElement

    lifecycle.device.observation = lifecycle.device.observation.model_copy(
        update={"elements": (AxElement(element_ref="celem_1", depth=1, role="axbutton"),)}
    )
    found = await application.discover("tex_observe")
    read = await application.observe(
        "tex_observe", found.targets[0].target_ref, include_image=False
    )
    lifecycle.device.actions = []

    async def execute(request, *, settings, authority):
        authority()
        lifecycle.device.actions.append(request)
        return ActionOutcome(status="completed", delivery=request.delivery)

    lifecycle.device.execute_one = execute
    return application, lifecycle, read


async def test_application_requires_actual_consumed_action_approval(environment):
    action_ledger(environment)
    app, lifecycle, read = await app_ready(environment)
    result = await app.execute_one(
        "tex_action",
        read.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
    )
    assert result.status == "completed" and len(lifecycle.device.actions) == 1
    again = await app.execute_one(
        "tex_action",
        read.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
    )
    assert again.error_code == "execution_already_used" and len(lifecycle.device.actions) == 1


async def test_missing_approval_never_reaches_sdk_and_stops_admission(environment):
    action_ledger(environment, approved=False)
    app, lifecycle, read = await app_ready(environment)
    result = await app.execute_one(
        "tex_action",
        read.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
    )
    assert result.status == "not_started" and result.error_code == "execution_not_authorized"
    assert lifecycle.device.actions == [] and lifecycle.device.invalidated


async def test_revocation_after_dispatch_preserves_effect_and_stops_new_entry(environment):
    action_ledger(environment)
    app, lifecycle, read = await app_ready(environment)
    journal = environment[1]
    grant = journal.get_capability_grant("ws_a", environment[-1].grant_id)

    async def revoke(request, *, settings, authority):
        authority()
        lifecycle.device.actions.append(request)
        journal.save_capability_grant(
            "ws_a",
            grant.model_copy(
                update={
                    "revoked_at": NOW,
                    "revocation_reason": "stop",
                    "row_version": 2,
                }
            ),
            expected_row_version=1,
        )
        return ActionOutcome(status="completed", delivery=request.delivery)

    lifecycle.device.execute_one = revoke
    result = await app.execute_one(
        "tex_action",
        read.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
    )
    assert result.status == "completed" and result.error_code == "grant_inactive"
    assert lifecycle.device.invalidated
    assert len(lifecycle.device.actions) == 1


async def test_expiry_before_action_never_enters_device(environment):
    service, device, clock, authority, read = await ready(environment)
    clock.value += timedelta(seconds=30)
    outcome = await service.execute_one(
        read.observation.observation_id, ClickAction(type="click", x=1, y=1), authority=authority
    )
    assert outcome.status == "not_started" and outcome.error_code == "stale_observation"
    assert device.actions == []


async def test_cancelled_execution_after_approval_cannot_enter_device(environment):

    action_ledger(environment)
    app, lifecycle, read = await app_ready(environment)
    environment[1].transact(
        lambda txn: txn.request_execution_cancellation_in_txn(
            "ws_a",
            "tex_action",
            now=NOW,
            reason="stop",
        )
    )
    outcome = await app.execute_one(
        "tex_action",
        read.observation.observation_id,
        ClickAction(type="click", element_ref="celem_1"),
    )
    assert outcome.status == "not_started" and outcome.error_code == "execution_cancelled"
    assert lifecycle.device.actions == []


async def test_runtime_operation_limit_applies_to_action_without_entering_device(environment):
    service, device, _, authority, read = await ready(environment, max_operations=2)
    result = await service.execute_one(
        read.observation.observation_id, ClickAction(type="click", x=1, y=1), authority=authority
    )
    assert result.status == "not_started" and result.error_code == "operation_budget"
    assert device.actions == [] and device.invalidated


async def test_consumed_action_execution_cannot_be_reused_with_a_new_observation(environment):
    action_ledger(environment)
    app, lifecycle, read = await app_ready(environment)
    action = ClickAction(type="click", element_ref="celem_1")
    assert (
        await app.execute_one("tex_action", read.observation.observation_id, action)
    ).status == "completed"
    lifecycle.device.observation = lifecycle.device.observation.model_copy(
        update={"observation_id": "cobs_2"}
    )
    fresh = await app.observe("tex_observe", read.observation.target_ref, include_image=False)
    outcome = await app.execute_one("tex_action", fresh.observation.observation_id, action)
    assert outcome.status == "not_started" and outcome.error_code == "execution_already_used"
    assert len(lifecycle.device.actions) == 1

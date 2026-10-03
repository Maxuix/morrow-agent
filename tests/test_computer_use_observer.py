"""Run-bound observation admission, deadlines and revocation with injected ports."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from morrow.application.computer_authorization import authorize_computer_execution
from morrow.core.computer_use import (
    ComputerUseAppIdentity,
    ComputerUseContractError,
    DiscoverResult,
    ObservedWindow,
    RunSession,
    TargetRef,
)
from morrow.core.execution import EffectClass, ToolExecutionState
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.durable_log import durable_call_id
from morrow.services.computer_use import ComputerUseRunService
from morrow.testing import FixedClock
from test_computer_use_permissions import NOW
from test_computer_visual_service import environment as _visual_environment
from test_stage4_tool_journal import _execution, _intent


@pytest.fixture
def environment(tmp_path):
    with_context = _visual_environment.__wrapped__(tmp_path)
    values = next(with_context)
    _, journal, _, _, _, original = values
    journal.put_execution(
        "ws_a",
        _execution(
            intent=_intent(
                tool_name="computer_observe",
                call_id=durable_call_id("observe_call"),
                ordinal=2,
                effect_class=EffectClass.BOUNDED_EXTERNAL_READ,
                requires_approval=False,
            ),
            tool_execution_id="tex_observe",
            state=ToolExecutionState.EXECUTING,
            permission_snapshot_id=original.permission_snapshot_id,
            grant_id=original.grant_id,
            isolation=original.isolation,
        ),
    )
    try:
        yield values
    finally:
        with_context.close()


def _authorize(journal, scope):
    return authorize_computer_execution(
        journal,
        workspace_id="ws_a",
        execution_id="tex_observe",
        scope=scope,
        tool_name="computer_observe",
        now=NOW,
    )


class _Device:
    def __init__(self, observation, capture):
        self.observation, self.capture = observation, capture
        self.calls = []
        self.invalidated = False
        self.after_read = lambda: None
        self.target = TargetRef(
            target_ref=observation.target_ref,
            agent_run_id=observation.agent_run_id,
            generation=observation.generation,
            app=ComputerUseAppIdentity(bundle_id=observation.bundle_id),
            process_identity=observation.process_identity,
            window_identity=observation.window_identity,
        )

    async def discover(self, admitted):
        request = admitted.request
        self.calls.append(request)
        return DiscoverResult(targets=(self.target,))

    async def observe(self, admitted):
        request = admitted.request
        self.calls.append(request)
        self.after_read()
        return ObservedWindow(self.observation, self.capture if request.include_image else None)

    def invalidate(self):
        self.invalidated = True


def _service(environment, **settings):
    _, journal, scope, capture, observation, _ = environment
    device = _Device(observation, capture)
    clock = FixedClock(NOW)
    params = {"enabled": True, "mode": ComputerUseMode.HYBRID}
    params.update(settings)
    service = ComputerUseRunService(
        device,
        RunSession(run_session_id="crun_1", agent_run_id="arun_1", generation=1),
        scope,
        ComputerUseSettings(**params),
        clock,
    )
    return service, device, clock, lambda: _authorize(journal, scope)


async def test_authorized_discover_and_observe_share_frozen_scope_and_transient_capture(
    environment,
):
    service, device, _, authority = _service(environment)
    found = await service.discover(authority=authority)
    read = await service.observe(found.targets[0].target_ref, authority=authority)
    assert read.capture is environment[3]
    assert read.observation.image is None
    assert "content" not in repr(read)
    assert all(request.scope == environment[2] for request in device.calls)
    assert device.calls[1].delivery is environment[2].delivery


@pytest.mark.parametrize("scope_change", [{"generation": 2}, {"agent_run_id": "arun_other"}])
async def test_changed_scope_never_enters_device(environment, scope_change):
    service, device, _, _ = _service(environment)
    _, journal, scope, *_ = environment
    changed = scope.model_copy(update=scope_change)
    with pytest.raises(ComputerUseContractError):
        await service.discover(authority=lambda: _authorize(journal, changed))
    assert device.calls == []


async def test_unknown_target_and_ungranted_app_never_enter_device(environment):
    service, device, _, authority = _service(environment)
    with pytest.raises(ComputerUseContractError, match="unknown_target"):
        await service.observe("ctarget_forged", authority=authority)
    with pytest.raises(ComputerUseContractError, match="app_not_granted"):
        await service.discover(authority=authority, bundle_id="com.other.App")
    assert device.calls == []


async def test_provider_image_mode_is_explicit(environment):
    service, device, _, authority = _service(environment, mode=ComputerUseMode.SEMANTIC)
    await service.discover(authority=authority)
    read = await service.observe(device.target.target_ref, authority=authority)
    assert read.capture is None
    before = len(device.calls)
    with pytest.raises(ComputerUseContractError, match="images_not_allowed"):
        await service.observe(device.target.target_ref, authority=authority, include_image=True)
    assert len(device.calls) == before


async def test_operation_budget_is_consumed_before_device_work(environment):
    service, device, _, authority = _service(environment, max_operations=1)
    await service.discover(authority=authority)
    with pytest.raises(ComputerUseContractError, match="operation_budget"):
        await service.observe(device.target.target_ref, authority=authority)
    assert len(device.calls) == 1
    assert device.invalidated


@pytest.mark.parametrize("seconds", [600, -1])
async def test_clock_boundary_refuses_device_admission(environment, seconds):
    service, device, clock, authority = _service(environment)
    clock.value += timedelta(seconds=seconds)
    with pytest.raises(ComputerUseContractError, match="run_budget"):
        await service.discover(authority=authority)
    assert not device.calls
    assert device.invalidated


async def test_deadline_expiring_during_read_returns_no_observation(environment):
    service, device, clock, authority = _service(environment)
    await service.discover(authority=authority)
    device.after_read = lambda: setattr(clock, "value", NOW + timedelta(seconds=600))
    with pytest.raises(ComputerUseContractError, match="run_budget"):
        await service.observe(device.target.target_ref, authority=authority)
    assert device.invalidated
    assert service._observations == {}


async def test_revocation_during_read_rejects_capture_and_stops_session(environment):
    service, device, _, authority = _service(environment)
    _, journal, *_ = environment
    await service.discover(authority=authority)
    execution = journal.get_execution("ws_a", "tex_1")
    grant = journal.get_capability_grant("ws_a", execution.grant_id)
    device.after_read = lambda: journal.save_capability_grant(
        "ws_a",
        grant.model_copy(update={"revoked_at": NOW, "revocation_reason": "stop", "row_version": 2}),
        expected_row_version=1,
    )
    with pytest.raises(ComputerUseContractError, match="grant_inactive"):
        await service.observe(device.target.target_ref, authority=authority)
    assert device.invalidated
    assert service._observations == {}


async def test_foreign_device_observation_is_not_registered(environment):
    service, device, _, authority = _service(environment)
    await service.discover(authority=authority)
    device.observation = device.observation.model_copy(update={"process_identity": "cproc_other"})
    with pytest.raises(ComputerUseContractError, match="subject_mismatch"):
        await service.observe(device.target.target_ref, authority=authority)
    assert service._observations == {}


async def test_concurrent_observe_is_refused_without_queue_or_second_call(environment):
    service, device, _, authority = _service(environment)
    await service.discover(authority=authority)
    entered, release = asyncio.Event(), asyncio.Event()
    original = device.observe

    async def held(request):
        entered.set()
        await release.wait()
        return await original(request)

    device.observe = held
    first = asyncio.create_task(service.observe(device.target.target_ref, authority=authority))
    await entered.wait()
    try:
        with pytest.raises(ComputerUseContractError, match="desktop_busy"):
            await service.observe(device.target.target_ref, authority=authority)
    finally:
        release.set()
        await first
    assert len(device.calls) == 2


class _Lifecycle:
    def __init__(self, device):
        self.device = device
        self.calls = []
        self.after_open = lambda: None

    async def open_run_session(self, request):
        self.calls.append("open")
        self.after_open()
        return RunSession(run_session_id="crun_1", agent_run_id="arun_1", generation=1)

    def session_for(self, run):
        self.calls.append("session")
        return self.device

    async def close_run_session(self, request):
        self.calls.append("close")


def _application(environment, lifecycle=None):
    from morrow.application.computer_use import ComputerUseObservationService

    _, journal, scope, capture, observation, _ = environment
    lifecycle = lifecycle or _Lifecycle(_Device(observation, capture))
    application = ComputerUseObservationService(
        lifecycle,
        journal,
        scope,
        ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        FixedClock(NOW),
    )
    return application, lifecycle


async def test_application_reuses_one_run_session_and_closes_it(environment):
    application, lifecycle = _application(environment)
    found = await application.discover("tex_observe")
    await application.discover("tex_observe")
    read = await application.observe("tex_observe", found.targets[0].target_ref)
    assert read.capture is environment[3]
    assert lifecycle.calls == ["open", "session"]
    await application.close()
    await application.close()
    assert lifecycle.calls == ["open", "session", "close"]
    assert lifecycle.device.invalidated
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await application.discover("tex_observe")


async def test_application_unknown_refs_and_unauthorized_execution_do_not_initialize_native(
    environment,
):
    application, lifecycle = _application(environment)
    with pytest.raises(ComputerUseContractError, match="unknown_target"):
        await application.observe("tex_observe", "ctarget_1")
    with pytest.raises(ComputerUseContractError, match="execution_not_authorized"):
        await application.discover("tex_other")
    with pytest.raises(ComputerUseContractError, match="app_not_granted"):
        await application.discover("tex_observe", bundle_id="com.other.App")
    assert lifecycle.calls == []
    assert lifecycle.device.calls == []


async def test_application_revocation_while_opening_closes_session_before_any_window_read(
    environment,
):
    application, lifecycle = _application(environment)
    _, journal, *_ = environment
    execution = journal.get_execution("ws_a", "tex_1")
    grant = journal.get_capability_grant("ws_a", execution.grant_id)
    lifecycle.after_open = lambda: journal.save_capability_grant(
        "ws_a",
        grant.model_copy(update={"revoked_at": NOW, "revocation_reason": "stop", "row_version": 2}),
        expected_row_version=1,
    )
    with pytest.raises(ComputerUseContractError, match="grant_inactive"):
        await application.discover("tex_observe")
    assert lifecycle.calls == ["open", "close"]
    assert lifecycle.device.calls == []


async def test_application_native_gate_stays_closed_before_driver_construction(environment):
    from morrow.application.computer_use import ComputerUseLifecycle
    from morrow.core.computer_use import ComputerUsePreflight

    def forbidden():
        pytest.fail("native factory called before gate")

    lifecycle = ComputerUseLifecycle(
        forbidden, lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified")
    )
    application, _ = _application(environment, lifecycle=lifecycle)
    with pytest.raises(ComputerUseContractError, match="native_unverified"):
        await application.discover("tex_observe")


async def test_cancelled_observation_invalidates_run_and_never_retries(environment):
    service, device, _, authority = _service(environment)
    await service.discover(authority=authority)
    entered = asyncio.Event()

    async def held(request):
        entered.set()
        await asyncio.Event().wait()

    device.observe = held
    pending = asyncio.create_task(service.observe(device.target.target_ref, authority=authority))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert device.invalidated
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await service.observe(device.target.target_ref, authority=authority)
    assert len(device.calls) == 1


def test_run_settings_scope_are_frozen_after_construction(environment):
    service, *_ = _service(environment)
    with pytest.raises(AttributeError):
        service.settings = ComputerUseSettings(enabled=True, max_operations=1)
    with pytest.raises(AttributeError):
        service.scope = environment[2].model_copy(update={"generation": 2})


@pytest.mark.parametrize("during_read", [False, True])
async def test_durable_execution_cancellation_is_rechecked_before_and_after_read(
    environment, during_read
):
    service, device, _, authority = _service(environment)
    _, journal, *_ = environment
    await service.discover(authority=authority)

    def cancel():
        journal.transact(
            lambda txn: txn.request_execution_cancellation_in_txn(
                "ws_a", "tex_observe", now=NOW, reason="stop"
            )
        )

    before = len(device.calls)
    if during_read:
        device.after_read = cancel
    else:
        cancel()
    with pytest.raises(ComputerUseContractError, match="execution_cancelled"):
        await service.observe(device.target.target_ref, authority=authority)
    assert len(device.calls) == before + int(during_read)
    assert service._observations == {}


async def test_wrong_effect_declaration_cannot_enter_computer_handler(environment):
    application, lifecycle = _application(environment)
    # Original image-evidence fixture has a synthetic file-write intent and no consumed approval.
    with pytest.raises(ComputerUseContractError, match="execution_not_authorized"):
        await application.discover("tex_1")
    assert lifecycle.calls == []


async def test_returned_capture_cannot_exceed_frozen_image_dimension_budget(environment):
    service, device, _, authority = _service(environment, image_long_edge_px=2)
    await service.discover(authority=authority)
    with pytest.raises(ComputerUseContractError, match="image_budget"):
        await service.observe(device.target.target_ref, authority=authority)
    assert service._observations == {}


async def test_semantic_mode_refuses_unrequested_capture_from_port(environment):
    service, device, _, authority = _service(environment, mode=ComputerUseMode.SEMANTIC)
    await service.discover(authority=authority)

    async def unexpected(request):
        return ObservedWindow(device.observation, device.capture)

    device.observe = unexpected
    with pytest.raises(ComputerUseContractError, match="images_not_allowed"):
        await service.observe(device.target.target_ref, authority=authority)
    assert service._observations == {}


async def test_application_publishes_real_pixels_and_only_safe_durable_references(environment):
    import io

    from PIL import Image

    visuals = environment[0]
    application, lifecycle = _application(environment)
    found = await application.discover("tex_observe")
    observation, references = await application.observe_published(
        "tex_observe",
        found.targets[0].target_ref,
        visuals=visuals,
    )
    (reference,) = references
    assert observation.image.artifact_id == reference.artifact_id
    metadata = environment[1].get_artifact("ws_a", reference.artifact_id)
    assert metadata is not None
    stored = visuals.artifacts.read(reference.artifact_id, max_bytes=reference.byte_size).content
    with Image.open(io.BytesIO(stored)) as image:
        assert image.size == (8, 6)
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert "sensitive_regions" not in observation.model_dump_json()
    assert lifecycle.calls == ["open", "session"]


async def test_semantic_observation_does_not_publish_an_artifact(environment):
    application, _ = _application(environment)
    found = await application.discover("tex_observe")
    observation, references = await application.observe_published(
        "tex_observe",
        found.targets[0].target_ref,
        visuals=environment[0],
        include_image=False,
    )
    assert observation.image is None
    assert references == ()
    assert environment[1].list_artifacts("ws_a", task_run_id="task_1") == ()


async def test_application_preserves_pixels_for_password_controls(environment):
    import io

    from PIL import Image

    from morrow.core.artifacts import ArtifactSensitivity
    from morrow.core.computer_use import AxElement

    application, lifecycle = _application(environment)
    original = lifecycle.device.observation
    lifecycle.device.observation = original.model_copy(
        update={
            "elements": (
                AxElement(
                    element_ref="celem_1",
                    depth=1,
                    role="axsecuretextfield",
                    label="password=synthetic",
                ),
            ),
        }
    )

    async def content_read(request):
        return ObservedWindow(
            lifecycle.device.observation,
            lifecycle.device.capture,
        )

    lifecycle.device.observe = content_read
    found = await application.discover("tex_observe")
    _, references = await application.observe_published(
        "tex_observe",
        found.targets[0].target_ref,
        visuals=environment[0],
    )
    (reference,) = references
    metadata = environment[1].get_artifact("ws_a", reference.artifact_id)
    assert metadata.sensitivity is ArtifactSensitivity.UNCLASSIFIED
    content = (
        environment[0].artifacts.read(reference.artifact_id, max_bytes=reference.byte_size).content
    )
    with Image.open(io.BytesIO(content)) as image:
        assert image.getpixel((1, 1)) == (255, 0, 0)
        assert image.getpixel((4, 3)) == (255, 0, 0)
        assert image.getpixel((5, 3)) == (255, 0, 0)
    assert "sensitive_regions" not in metadata.model_dump_json()


@pytest.mark.parametrize("image_error", ["unknown_scale", "image_decode"])
async def test_unconfirmed_capture_does_not_create_artifact(environment, image_error):
    application, lifecycle = _application(environment)

    async def unsafe_read(request):
        return ObservedWindow(
            lifecycle.device.observation, lifecycle.device.capture, image_error=image_error
        )

    lifecycle.device.observe = unsafe_read
    found = await application.discover("tex_observe")
    with pytest.raises(ComputerUseContractError, match=image_error):
        await application.observe_published(
            "tex_observe",
            found.targets[0].target_ref,
            visuals=environment[0],
        )
    assert environment[1].list_artifacts("ws_a", task_run_id="task_1") == ()

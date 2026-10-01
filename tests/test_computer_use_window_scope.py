"""Explicit local windows remain frozen across grant JSON and native discovery."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.application.computer_requests import ComputerUseSelection
from morrow.core.computer_use import (
    ComputerUseContractError,
    ComputerUseScope,
    ComputerUseWindowIdentity,
    DiscoverRequest,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    observe_window_if_admitted,
    prepare_execute_request,
)
from morrow.core.domain import canonical_json_bytes
from morrow.core.permissions import (
    CapabilityName,
    decode_capability_payload,
    encode_capability_payload,
)
from test_computer_use_candidates import AUTH, SETTINGS, Driver, owner_for
from test_computer_use_contracts import _execute
from test_computer_use_contracts import _scope as contract_scope
from test_computer_use_driver import _Native, _scope
from test_computer_use_lifecycle import _close


def window_scope(windows):
    selection = ComputerUseSelection(apps=_scope().apps, windows=windows)
    return selection.bind(
        workspace_id="ws_1", task_run_id="task_1", agent_run_id="arun_1", generation=1
    )


def test_window_scope_roundtrip_and_legacy_scope_bytes_are_preserved():
    legacy = _scope()
    payload = legacy.model_dump(mode="json")
    assert "windows" not in payload and payload["schema_version"] == 1
    original_bytes = (
        b'{"agent_run_id":"arun_1","apps":[{"bundle_id":"com.example.Notes"}],'
        b'"delivery":"foreground","generation":1,"image_share":"controlled_window",'
        b'"operations":["observe","action"],"schema_version":1,"task_run_id":"task_1",'
        b'"window_boundary":"window","workspace_id":"ws_1"}'
    )
    assert canonical_json_bytes(payload) == original_bytes
    assert canonical_json_bytes(
        ComputerUseScope.model_validate(payload).model_dump(mode="json")
    ) == canonical_json_bytes(payload)
    windows = (ComputerUseWindowIdentity(app=legacy.apps[0], window_identity="cwin_selected"),)
    scope = window_scope(windows)
    assert scope.schema_version == 2 and scope.windows == windows
    assert ComputerUseScope.model_validate_json(scope.model_dump_json()) == scope
    encoded = encode_capability_payload(
        schema_version=10,
        capabilities=(CapabilityName.COMPUTER_USE_HOST,),
        computer_use_scope=scope,
    )
    capabilities, restored = decode_capability_payload(encoded, schema_version=10)
    assert restored == scope and capabilities == (CapabilityName.COMPUTER_USE_HOST,)
    for invalid in [
        payload | {"schema_version": 2},
        payload | {"windows": [windows[0].model_dump(mode="json")]},
        scope.model_dump(mode="json") | {"windows": []},
        scope.model_dump(mode="json") | {"windows": [windows[0].model_dump(mode="json")] * 2},
    ]:
        with pytest.raises(ValidationError):
            ComputerUseScope.model_validate(invalid)


async def test_selected_native_window_is_the_only_discovered_window_of_the_app():
    class TwoWindows(_Native):
        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            result.windows[0].title = "First"
            result.windows.append(
                SimpleNamespace(
                    window_id=9002,
                    pid=4242,
                    title="Second",
                    is_on_screen=True,
                    minimized=False,
                    bounds=SimpleNamespace(x=0, y=0, width=40, height=20),
                )
            )
            return result

    class LocalDriver(TwoWindows, Driver):
        pass

    owner, lease, _, sessions = owner_for(LocalDriver())
    owner._session_factory = lambda driver, name: TwoWindows()
    try:
        candidates = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        assert [item.display_label for item in candidates.candidates] == ["First", "Second"]
        selected = owner.select_local_candidates(
            (candidates.candidates[1].candidate_id,), authority=AUTH
        )
        scope = window_scope(selected)
        run = await owner.open_run_session(
            OpenRunSessionRequest(authority=AUTH, scope=scope, agent_run_id="arun_1")
        )
        session = owner.session_for(run)
        result = await session.discover(
            DiscoverRequest(authority=AUTH, scope=scope, run_session_id=run.run_session_id)
        )
        assert len(result.targets) == 1
        target = result.targets[0]
        assert (
            target.display_label == "Second"
            and target.window_identity == selected[0].window_identity
        )
        native = session._registry.window(target.window_identity)
        assert native.window_id == 9002
        assert sessions == [] and lease.held
        with pytest.raises(ComputerUseContractError, match="desktop_busy"):
            owner.select_local_candidates((candidates.candidates[0].candidate_id,), authority=AUTH)
        await owner.close_run_session(_close(run))
        with pytest.raises(ComputerUseContractError, match="unknown_target"):
            await owner.open_run_session(
                OpenRunSessionRequest(
                    authority=AUTH,
                    scope=scope.model_copy(update={"generation": 2}),
                    agent_run_id="arun_1",
                )
            )
        assert not lease.held and not owner.quarantined
    finally:
        await owner.shutdown()


@pytest.mark.parametrize("fault", ["expired", "refreshed", "pid_changed"])
async def test_stale_window_selection_fails_before_sdk_session(fault):
    birth = [ProcessBirth(1, 0)]
    owner, lease, clock, sessions = owner_for(Driver(), process_reader=lambda pid: birth[0])
    try:
        candidates = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        windows = owner.select_local_candidates(
            (candidates.candidates[0].candidate_id,), authority=AUTH
        )
        if fault == "expired":
            clock.value = candidates.expires_at
        elif fault == "refreshed":
            await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        else:
            birth[0] = ProcessBirth(2, 0)
        with pytest.raises(ComputerUseContractError, match="stale_observation|unknown_target"):
            await owner.open_run_session(
                OpenRunSessionRequest(
                    authority=AUTH, scope=window_scope(windows), agent_run_id="arun_1"
                )
            )
        assert not lease.held and not owner.quarantined and sessions == []
    finally:
        await owner.shutdown()


def test_unselected_window_is_rejected_by_observe_and_action_contracts():
    request = _execute({"type": "click", "element_ref": "celem_1", "button": "left", "count": 1})
    scope = contract_scope(
        schema_version=2,
        apps=(request.target.app,),
        windows=(
            ComputerUseWindowIdentity(app=request.target.app, window_identity="cwin_selected"),
        ),
    )

    class Port:
        def observe_window(self, request):
            raise AssertionError("device should never be reached")

    with pytest.raises(ComputerUseContractError, match="window_not_granted"):
        observe_window_if_admitted(
            Port(),
            ObserveWindowRequest(
                authority=AUTH, scope=scope, target=request.target, delivery=scope.delivery
            ),
            settings=SETTINGS,
        )
    with pytest.raises(ComputerUseContractError, match="window_not_granted"):
        prepare_execute_request(request.model_copy(update={"scope": scope}), settings=SETTINGS)

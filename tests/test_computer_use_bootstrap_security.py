"""Production composition uses the guarded typed session with a fake SDK."""

import json
from types import SimpleNamespace

from morrow.adapters.computer_use import sdk_loader
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application, build_computer_use_lifecycle
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    DiscoverRequest,
    ExecuteRequest,
    ObserveWindowRequest,
    TypeTextAction,
)
from morrow.core.runtime_policy import ComputerUseSettings
from test_computer_use_actions import Native, effects
from test_computer_use_driver import _process_birth, _sdk
from test_computer_use_lifecycle import _close, _Lease, _open
from test_computer_use_security import response


async def test_bootstrap_owner_queries_exact_security_and_enforces_guarded_input(
    tmp_path, monkeypatch
):
    queries = []

    class GuardedNative(Native):
        async def call_tool(self, name, content):
            if name == "get_element_security":
                queries.append(json.loads(content))
                return SimpleNamespace(is_error=False, structured_json=response())
            return await super().call_tool(name, content)

        async def shutdown(self):
            pass

    native = GuardedNative()
    sdk = _sdk()
    sdk.ConfiguredDriverOptions = SimpleNamespace
    sdk.RuntimeAuthorizationOptions = SimpleNamespace
    sdk.TrustedSessionOptions = SimpleNamespace
    sdk.SessionPermissionMode = SimpleNamespace(STANDARD="standard")
    sdk.CuaDriver = SimpleNamespace(create_configured=lambda options: native)
    sdk.create_trusted_session = lambda driver, options: driver
    monkeypatch.setattr(sdk_loader, "load_sdk", lambda: sdk)
    app = build_application(state_root=tmp_path, credentials=MemoryCredentialStore())
    settings = ComputerUseSettings(enabled=True)
    lifecycle = build_computer_use_lifecycle(app, settings)
    assert lifecycle.runtime_status.state == "not_activated"
    assert lifecycle._native_verified is False
    # Inspect the real production owner factory; activation remains closed.
    owner = lifecycle._factory()
    owner._process_reader = _process_birth
    owner._lease = _Lease()
    request = _open()
    try:
        run = await owner.open_run_session(request)
        session = owner.session_for(run)
        found = await session.discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=request.scope,
                run_session_id=run.run_session_id,
            )
        )
        target = found.targets[0]
        read = await session.observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=request.scope,
                target=target,
                delivery=request.scope.delivery,
                include_image=False,
            )
        )
        outcome = await session.execute_one(
            ExecuteRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=request.scope,
                target=target,
                observation=read.observation,
                delivery=request.scope.delivery,
                action=TypeTextAction(
                    type="type_text",
                    text="controlled",
                    element_ref=read.observation.elements[0].element_ref,
                ),
            ),
            settings=settings,
            authority=lambda: None,
        )
        assert outcome.status == "completed"
        assert len(queries) == 3
        assert all(query["element_token"] == "tok-hidden" for query in queries)
        assert len(effects(native)) == 1
        assert effects(native)[0][1]["require_non_sensitive"] is True
        await owner.close_run_session(_close(run))
    finally:
        await owner.shutdown()

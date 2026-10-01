"""Real terminal selection and ordinary AgentLoop on one fake SDK owner loop."""

import io
from datetime import timedelta

import pytest
from rich.console import Console

from morrow.application.computer_selection import ComputerUseSelectionService
from morrow.application.computer_settings import ComputerUseSettingsService
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.bootstrap import build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import ComputerUsePreflight
from morrow.core.permissions import CapabilityName
from morrow.core.runtime_policy import ComputerUseSettings, RuntimePolicyOverrides
from morrow.interfaces.computer_picker import TerminalComputerPicker
from morrow.interfaces.terminal import Terminal, run_repl
from morrow.testing import ScriptedModelProvider
from test_agent_run_preparation import _app, _configure_active
from test_computer_use_candidates import Driver, owner_for
from test_computer_use_loop import tool


@pytest.mark.parametrize("mode", ["complete", "expired", "host_conflict", "invalid_delivery"])
async def test_terminal_selects_once_then_uses_existing_loop(tmp_path, mode):
    app = _app(tmp_path)
    providers = []

    def provider(config, credential):
        result = ScriptedModelProvider(
            [tool("discover", "computer_observe", {"operation": "discover"}), ["window found"]]
        )
        providers.append(result)
        return result

    app.registry.register(
        "fake-adapter",
        provider,
        capabilities=ProviderCapabilities(tool_protocol="openai_function", input_types=("text",)),
    )
    _configure_active(app)
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True)
                )
            }
        ),
        expected_revision=app.global_store.load().revision,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    driver = Driver()
    owner, lease, clock, sessions = owner_for(driver)
    lifecycle = ComputerUseLifecycle(
        lambda: owner,
        lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
        native_verified=True,
    )
    products = build_session_application(
        app,
        identity,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        computer_use_lifecycle=lifecycle,
    )
    journal = products.orchestrator.preparation.computer_factory.journal
    clock.value = journal.now()
    settings = ComputerUseSettingsService(app, preflight=lambda _: lifecycle.preflight())
    picker = TerminalComputerPicker(
        app, products, ComputerUseSelectionService(app, lifecycle, settings, clock)
    )
    output = io.StringIO()
    saved = {}
    answers = ["/computer", "1", "invalid" if mode == "invalid_delivery" else "foreground"]
    if mode != "invalid_delivery":
        answers += ["", ""]
        if mode == "host_conflict":
            answers += ["/grant"]
        answers += ["observe my window", "/computer status", "/computer clear"]
    answers += ["/exit"]

    class ScriptedTerminal(Terminal):
        def __init__(self):
            super().__init__(Console(file=output, width=200, force_terminal=False))

        async def prompt(self, prompt_session, message="你 > "):
            answer = answers.pop(0)
            if answer == "observe my window" and mode == "expired":
                clock.value += timedelta(seconds=31)
            if answer == "/exit":
                saved["grants"] = journal.list_capability_grants(identity.workspace_id)
            return answer

    try:
        assert (
            await run_repl(
                products.orchestrator,
                session=products.session,
                terminal=ScriptedTerminal(),
                prompt_session=object(),
                computer_picker=picker,
            )
            == 0
        )
        assert not answers and not lease.held
        grants = saved["grants"]
        requests = [request for value in providers for request in value.stream_calls]
        rendered = output.getvalue()
        assert "ccandidate" not in rendered and "cselection" not in rendered
        if mode in {"expired", "invalid_delivery"}:
            assert not grants and not requests and not sessions
            if mode == "expired":
                assert (
                    "下次运行窗口" in rendered
                )  # Refusal retains pending scope until explicit clear.
            else:
                assert "选择无效" in rendered
        else:
            assert len(grants) == 1 and grants[0].capabilities == (
                CapabilityName.COMPUTER_USE_HOST,
            )
            assert grants[0].computer_use_scope.schema_version == 2
            assert len(grants[0].computer_use_scope.windows) == 1
            assert len(sessions) == 1 and len(requests) == 2
            tools = [tool for value in providers for tool in value.stream_tools]
            desktop_tools = [
                item.function.name
                for item in tools[0]
                if item.function.name.startswith("computer_")
            ]
            assert desktop_tools == ["computer_observe"]
            assert "仅观察" in rendered and "不分享图像" in rendered
            assert "本机桌面状态（最近读取）：当前无桌面会话" in rendered
            assert "已经投递的效果无法撤回" in rendered
            if mode == "host_conflict":
                assert "清除桌面选择" in rendered
                assert products.session.pending_full_access_grant is False
        assert picker.pending is None
        assert [name for name, _ in driver.calls].count("shutdown") == 1
    finally:
        await lifecycle.shutdown()
        products.persistence.store_session.close()

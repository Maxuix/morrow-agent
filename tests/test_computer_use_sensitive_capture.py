"""Content-transparent captures with actual pixels; no native driver or network."""

from types import SimpleNamespace

import pytest

from morrow.core.computer_admission import admit_discover, admit_observe
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseDelivery,
    DiscoverRequest,
    ObserveWindowRequest,
    OpenRunSessionRequest,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.testing import FixedClock
from test_computer_use_driver import NOW, _bound_session, _Native, _selected_scope


class Native(_Native):
    async def list_windows(self, payload):
        result = await super().list_windows(payload)
        result.windows[0].bounds.x = -100
        result.windows[0].bounds.y = 50
        return result

    async def get_window_state(self, payload):
        state = await super().get_window_state(payload)
        state.degraded = False
        state.truncated = False
        state.total_element_count = state.returned_element_count = 2
        # SDK completeness is independent of image availability.
        state.elements_complete = False
        state.window_bounds.x = -100
        state.window_bounds.y = 50
        state.elements[1].frame = SimpleNamespace(x=-95.5, y=52.5, w=10, h=4)
        self.change(state)
        return state

    def change(self, state):
        pass


async def read(native):
    scope = _selected_scope()
    session = _bound_session(native, clock=FixedClock(NOW))
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id="arun_1",
            scope=scope,
        )
    )
    found = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                run_session_id=run.run_session_id,
            )
        )
    )
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
    return await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                target=found.targets[0],
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=True,
            ),
            settings=settings,
        ),
    )


@pytest.mark.parametrize(
    "role", ["AXTextField", "AXSecureTextField", "AXPasswordField", "AXMenuItem"]
)
@pytest.mark.parametrize(
    "value", ["password settings", "password=synthetic-only", "sk-synthetic-key-1234567890"]
)
async def test_content_and_control_roles_do_not_block_capture(role, value):
    native = Native()

    def change(state):
        state.elements[1].role = role
        state.elements[1].label = value
        state.elements[1].value = value
        state.elements[1].enabled = True
        state.elements[1].frame = None

    native.change = change
    result = await read(native)
    assert result.image_error is None and result.capture is not None
    element = result.observation.elements[1]
    assert element.label == value and element.value == value and element.enabled is True
    assert "sensitive" not in element.model_dump()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: setattr(s, "degraded", True),
        lambda s: setattr(s, "truncated", True),
        lambda s: setattr(s, "total_element_count", 3),
        lambda s: setattr(s.elements[0], "depth", 99),
    ],
)
async def test_partial_ax_tree_does_not_block_geometrically_valid_image(change):
    native = Native()
    native.change = change
    result = await read(native)
    assert result.image_error is None and result.capture is not None
    assert result.observation.complete is False


async def test_unreadable_sdk_value_stays_none_and_text_budget_marks_partial_tree():
    native = Native()

    def change(state):
        state.elements[1].value = None
        state.elements[1].role = "AXSecureTextField"

    native.change = change
    result = await read(native)
    assert result.observation.elements[1].value is None

    def oversized(state):
        state.elements[1].value = "界" * 4097

    native.change = oversized
    result = await read(native)
    assert result.observation.truncated and result.observation.omitted_count > 0
    assert result.capture is not None and result.image_error is None

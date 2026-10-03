"""Trusted screen-point masks with actual pixels; no native driver or network."""

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
        # Unknown completeness does not assert that no other secrets exist.
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


async def test_ax_screen_points_map_outward_to_delivered_downscaled_image():
    result = await read(Native())
    assert result.image_error is None
    (region,) = result.sensitive_regions
    assert (region.left, region.top, region.right, region.bottom) == (2, 1, 8, 4)
    assert region.element_ref == result.observation.elements[1].element_ref
    assert result.observation.frame.scale_x == 1  # SDK action space is separate.
    assert "-95" not in repr(result)
    assert repr(region) == "SensitiveCaptureRegion()"
    assert "sensitive_regions" not in result.observation.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: setattr(s.elements[1], "frame", None),
        lambda s: setattr(s.elements[1].frame, "x", float("nan")),
        lambda s: setattr(s, "degraded", True),
        lambda s: setattr(s, "truncated", True),
        lambda s: setattr(s, "total_element_count", 3),
        lambda s: setattr(s.elements[0], "depth", 99),
    ],
)
async def test_unconfirmed_regions_or_omissions_cannot_publish_image(change):
    native = Native()
    native.change = change
    result = await read(native)
    assert result.image_error == "image_safety_unconfirmed"
    assert result.sensitive_regions == ()
    assert all(
        element.label is None for element in result.observation.elements if element.sensitive
    )


async def test_known_secret_in_value_is_sensitive_without_exposing_the_value():
    native = Native()

    def change(state):
        state.elements[1].role = "AXTextField"
        state.elements[1].label = "Account"
        state.elements[1].value = "password=do-not-export"

    native.change = change
    result = await read(native)
    assert result.image_error is None
    assert len(result.sensitive_regions) == 1
    assert result.observation.elements[1].sensitive
    assert "do-not-export" not in result.observation.model_dump_json()


@pytest.mark.parametrize(
    "x,width,expected", [(-101, 10, (0, 1, 5, 4)), (-95.5, 100, (2, 1, 20, 4)), (-200, 10, None)]
)
async def test_ax_panels_mask_only_their_intersection_with_captured_window(x, width, expected):
    native = Native()

    def change(state):
        state.elements[1].frame.x = x
        state.elements[1].frame.w = width

    native.change = change
    result = await read(native)
    assert result.image_error is None
    if expected is None:
        assert result.sensitive_regions == ()
    else:
        (region,) = result.sensitive_regions
        assert (region.left, region.top, region.right, region.bottom) == expected

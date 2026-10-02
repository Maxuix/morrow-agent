"""Sanitized predicate evidence and bounded polling without timing sleeps."""

import asyncio
from datetime import timedelta

import pytest

from morrow.core.computer_use import (
    AxElement,
    ClickAction,
    ObservedWindow,
    parse_computer_action,
)
from morrow.core.runtime_policy import ComputerUseMode
from morrow.services.computer_verification import evaluate_postcondition
from test_computer_use_after_action import revoke, setup
from test_computer_use_observer import environment as _observer_environment


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


def predicate(kind="attribute_equals", **fields):
    return parse_computer_action(
        {
            "type": "click",
            "element_ref": "celem_1",
            "postcondition": {"type": kind, **fields},
        }
    ).postcondition


@pytest.mark.parametrize(
    "complete,matches,enabled,expected",
    [
        (True, 1, True, "passed"),
        (True, 1, False, "failed"),
        (True, 1, None, "not_checked"),
        (False, 1, True, "not_checked"),
        (True, 2, True, "not_checked"),
        (True, 0, True, "failed"),
    ],
)
def test_attribute_requires_complete_unique_known_non_sensitive_match(
    environment,
    complete,
    matches,
    enabled,
    expected,
):
    observation = environment[4].model_copy(
        update={
            "complete": complete,
            "elements": tuple(
                AxElement(
                    element_ref=f"celem_{index}",
                    depth=1,
                    role="axbutton",
                    label="Ready",
                    enabled=enabled,
                )
                for index in range(matches)
            ),
        }
    )
    check = predicate(
        selector={"role": "axbutton", "label": "Ready"}, attribute="enabled", value="true"
    )
    assert evaluate_postcondition(observation, check) == expected


def test_sensitive_partial_degraded_and_old_refs_never_prove_attribute_identity(environment):
    check = predicate(selector={"role": "axbutton"}, attribute="enabled", value="true")
    base = environment[4].model_copy(
        update={
            "elements": (
                AxElement(element_ref="celem_new", depth=1, role="axbutton", enabled=True),
                AxElement(element_ref="celem_secret", depth=1, role="axtextfield", sensitive=True),
            )
        }
    )
    # A secure text field's known role rules out a button selector.
    assert evaluate_postcondition(base, check) == "passed"
    label_only = predicate(selector={"label": "Ready"}, attribute="enabled", value="true")
    assert evaluate_postcondition(base, label_only) == "not_checked"
    assert (
        evaluate_postcondition(base.model_copy(update={"degraded": True}), check) == "not_checked"
    )
    old = predicate(element_ref="celem_1", attribute="enabled", value="true")
    assert evaluate_postcondition(base, old) == "not_checked"


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("element_exists", {"selector": {"label": "Ready"}}),
        ("text_appears", {"text": "Ready"}),
    ],
)
def test_positive_presence_is_proven_in_partial_tree_but_absence_is_not(environment, kind, fields):
    check = predicate(kind, **fields)
    found = environment[4].model_copy(
        update={
            "complete": False,
            "elements": (
                AxElement(element_ref="celem_new", depth=1, role="axbutton", label="Ready"),
            ),
        }
    )
    assert evaluate_postcondition(found, check) == "passed"
    missing = found.model_copy(update={"elements": ()})
    assert evaluate_postcondition(missing, check) == "pending"
    assert evaluate_postcondition(missing.model_copy(update={"complete": True}), check) == "failed"


def test_text_never_matches_across_labels_or_hidden_sensitive_content(environment):
    observation = environment[4].model_copy(
        update={
            "elements": (
                AxElement(element_ref="celem_a", depth=1, role="axstatictext", label="Rea"),
                AxElement(element_ref="celem_b", depth=1, role="axstatictext", label="dy"),
                AxElement(element_ref="celem_c", depth=1, role="axtextfield", sensitive=True),
            )
        }
    )
    assert evaluate_postcondition(observation, predicate("text_appears", text="Ready")) == "pending"


async def polling_setup(environment, *, succeeds_at=None, complete=True):
    app, device, before = await setup(environment)
    app._settings = app._settings.model_copy(update={"mode": ComputerUseMode.SEMANTIC})
    app._run._settings = app._settings
    reads, waits = [], []

    async def read(request, *, settings):
        reads.append(request)
        index = len(reads)
        observation = device.observation.model_copy(
            update={
                "observation_id": f"cobs_poll{index}",
                "complete": complete,
                "elements": (
                    AxElement(
                        element_ref=f"celem_poll{index}",
                        depth=1,
                        role="axstatictext",
                        label="Ready"
                        if succeeds_at is not None and index >= succeeds_at
                        else "Waiting",
                    ),
                ),
            }
        )
        return ObservedWindow(observation)

    async def wait(seconds):
        waits.append(seconds)
        app._clock.value += timedelta(seconds=seconds)

    device.observe = read
    app._verification_wait = wait
    action = ClickAction(
        type="click", element_ref="celem_1", postcondition=predicate("text_appears", text="Ready")
    )
    return app, device, before, reads, waits, action


async def run(app, before, action):
    return await app.execute_published(
        "tex_action", before.observation.observation_id, action, visuals=None
    )


async def test_polling_reads_new_snapshots_and_never_repeats_action(environment):
    app, device, before, reads, waits, action = await polling_setup(environment, succeeds_at=3)
    result, references = await run(app, before, action)
    assert result.outcome.status == "completed" and result.outcome.postcondition == "passed"
    assert result.observation.observation_id == "cobs_poll3"
    assert result.verification_error is None and result.observation_error is None
    assert len(reads) == 3 and waits == [0.5, 0.5] and len(device.actions) == 1
    assert references == () and all(
        read.target.target_ref == before.observation.target_ref for read in reads
    )


@pytest.mark.parametrize(
    "complete,status,error",
    [
        (True, "failed", "verification_failed"),
        (False, "not_checked", "verification_unavailable"),
    ],
)
async def test_polling_caps_at_ten_reads_and_preserves_native_completion(
    environment, complete, status, error
):
    app, device, before, reads, waits, action = await polling_setup(environment, complete=complete)
    result, _ = await run(app, before, action)
    assert result.outcome.status == "completed" and result.outcome.postcondition == status
    assert result.outcome.error_code is None and result.verification_error == error
    assert len(reads) == 10 and len(waits) == 9 and sum(waits) <= 5
    assert len(device.actions) == 1


async def test_injected_deadline_prevents_additional_read(environment):
    app, device, before, reads, waits, action = await polling_setup(environment)

    async def wait(seconds):
        app._clock.value += timedelta(seconds=5)

    app._verification_wait = wait
    result, _ = await run(app, before, action)
    assert result.outcome.status == "completed" and result.outcome.postcondition == "failed"
    assert len(reads) == 1 and len(device.actions) == 1


async def test_revocation_during_poll_wait_does_not_read_or_publish(environment):
    app, device, before, reads, waits, action = await polling_setup(environment)

    async def wait(seconds):
        revoke(environment)

    app._verification_wait = wait
    result, references = await run(app, before, action)
    assert result.outcome.status == "completed" and result.observation_error == "grant_inactive"
    assert result.observation is None and references == ()
    assert len(reads) == 1 and len(device.actions) == 1 and device.invalidated


async def test_polling_sequence_rejects_interleaved_discovery_without_queue(environment):
    app, device, before, reads, waits, action = await polling_setup(environment, succeeds_at=2)
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(seconds):
        entered.set()
        await release.wait()

    app._verification_wait = wait
    task = asyncio.create_task(run(app, before, action))
    await entered.wait()
    from morrow.core.computer_use import ComputerUseContractError

    with pytest.raises(ComputerUseContractError, match="desktop_busy"):
        await app.discover("tex_observe")
    release.set()
    result, _ = await task
    assert result.outcome.postcondition == "passed" and len(reads) == 2
    assert len(device.actions) == 1
    # Reservation was released normally; explicit discovery can now run.
    await app.discover("tex_observe")


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "element_exists", "selector": {}},
        {"type": "element_exists", "selector": {"label": "Ready", "pid": 12}},
        {"type": "element_exists", "selector": {"label": "Ready"}, "element_ref": "celem_1"},
        {
            "type": "attribute_equals",
            "selector": {"role": "axbutton"},
            "attribute": "value",
            "value": "true",
        },
        {
            "type": "attribute_equals",
            "selector": {"role": "axbutton"},
            "attribute": "enabled",
            "value": True,
        },
        {"type": "text_appears", "text": "test", "script": "private script"},
    ],
)
def test_predicate_schema_refuses_ambiguous_or_arbitrary_native_fields(condition):
    from morrow.core.computer_use import ComputerUseContractError

    with pytest.raises(ComputerUseContractError):
        parse_computer_action(
            {"type": "click", "element_ref": "celem_1", "postcondition": condition}
        )


async def test_poll_read_timeout_preserves_unknown_native_effect(environment):
    app, device, before, reads, waits, action = await polling_setup(environment)
    execute = device.execute_one

    async def unknown(request, *, settings, authority):
        result = await execute(request, settings=settings, authority=authority)
        return result.model_copy(update={"status": "unknown", "error_code": "action_interrupted"})

    device.execute_one = unknown
    observe = device.observe

    async def timeout(request, *, settings):
        if reads:
            raise TimeoutError()
        return await observe(request, settings=settings)

    device.observe = timeout
    result, references = await run(app, before, action)
    assert result.outcome.status == "unknown" and result.outcome.error_code == "action_interrupted"
    assert result.observation_error == "verification_timeout" and references == ()
    assert result.verification_error == "verification_unavailable" and device.invalidated
    assert len(device.actions) == 1


async def test_predicate_success_never_upgrades_unknown_native_completion(environment):
    app, device, before, reads, waits, action = await polling_setup(environment, succeeds_at=2)
    execute = device.execute_one

    async def unknown(request, *, settings, authority):
        result = await execute(request, settings=settings, authority=authority)
        return result.model_copy(update={"status": "unknown", "error_code": "action_interrupted"})

    device.execute_one = unknown
    result, _ = await run(app, before, action)
    assert result.outcome.postcondition == "passed" and result.outcome.status == "unknown"
    assert result.outcome.error_code == "action_interrupted" and len(device.actions) == 1


@pytest.mark.parametrize(
    "native_flag,expected",
    [
        (True, True),
        (False, False),
        (1, None),
        ("true", None),
    ],
)
async def test_adapter_projects_only_typed_sdk_boolean(native_flag, expected):
    from morrow.core.computer_use import ObserveWindowRequest
    from test_computer_use_actions import setup as adapter_setup

    session, native, _, before, request = await adapter_setup()
    execute_request = request(
        ClickAction(type="click", element_ref=before.observation.elements[0].element_ref)
    )
    original_read = native.get_window_state

    async def state(payload):
        result = await original_read(payload)
        result.elements[0].enabled = native_flag
        # The pinned contract does not expose these properties; never infer them
        # from arbitrary extra attributes or map selected to checked/focused.
        result.elements[0].focused = True
        result.elements[0].checked = True
        result.elements[0].expanded = True
        return result

    native.get_window_state = state
    after = await session.observe(
        ObserveWindowRequest(
            authority=execute_request.authority,
            scope=execute_request.scope,
            target=execute_request.target,
            delivery=execute_request.delivery,
            include_image=False,
        )
    )
    element = after.observation.elements[0]
    assert element.enabled is expected
    assert element.focused is None and element.checked is None and element.expanded is None
    assert element.sensitive is False

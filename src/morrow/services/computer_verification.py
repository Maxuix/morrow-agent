"""Bounded predicates over sanitized observations, without SDK identity guesses."""

from typing import Literal

from morrow.core.computer_use import Observation, Postcondition

VerificationState = Literal["passed", "failed", "pending", "not_checked"]


def evaluate_postcondition(observation: Observation, predicate: Postcondition) -> VerificationState:
    if observation.degraded:
        return "not_checked"
    exhaustive = (
        observation.complete and not observation.truncated and observation.omitted_count == 0
    )
    elements = tuple(element for element in observation.elements if not element.sensitive)
    if predicate.type == "text_appears":
        if any(predicate.text in (element.label or "") for element in elements):
            return "passed"
        # Hidden sensitive text can never establish absence or satisfy a predicate.
        if any(element.sensitive for element in observation.elements):
            return "pending"
        return "failed" if exhaustive else "pending"
    if predicate.selector is None:
        # The old element_ref identifies the consumed snapshot. A fresh node with
        # a matching role/index/label is not proof of that exact native identity.
        return "not_checked"
    selector = predicate.selector
    matches = tuple(
        element
        for element in elements
        if (selector.role is None or element.role == selector.role)
        and (selector.label is None or element.label == selector.label)
    )
    hidden_possible = any(
        element.sensitive and (selector.role is None or element.role == selector.role)
        for element in observation.elements
    )
    if predicate.type == "element_exists":
        if matches:
            return "passed"
        if hidden_possible:
            return "pending"
        return "failed" if exhaustive else "pending"
    # A property assertion needs one proven match in an exhaustive tree. Local
    # uniqueness in a truncated/partial tree does not prove window uniqueness.
    if not exhaustive or hidden_possible:
        return "not_checked"
    if not matches:
        return "failed"
    if len(matches) != 1:
        return "not_checked"
    actual = getattr(matches[0], predicate.attribute)
    if actual is None:
        return "not_checked"
    return "passed" if actual == (predicate.value == "true") else "failed"

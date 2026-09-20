"""Three-state model/generation selection encoding and the unified resolver.

D06: model and generation resolve independently through
node explicit -> agent explicit -> task session snapshot -> adapter default.
Each state is an explicit typed value; a bare null never carries two meanings.
Resolved values always carry their source marker, and an inherited value that
the exact Model cannot express surfaces a ``needs_default`` diagnostic instead
of being silently dropped or silently switching the model (T12/T13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, model_validator

from morrow.core.models import GenerationOptions, ModelRef, ProtocolModel, ReasoningEffort

SelectionSource = Literal["node", "agent", "session_snapshot", "adapter_default"]


class InheritSelection(ProtocolModel):
    """Follow the next level of the resolution chain."""

    mode: Literal["inherit"] = "inherit"


class ModelDefaultSelection(ProtocolModel):
    """Stop the chain here and use the Provider/Adapter omission behavior."""

    mode: Literal["model_default"] = "model_default"


class ExplicitModelSelection(ProtocolModel):
    mode: Literal["explicit"] = "explicit"
    value: ModelRef


class ExplicitGenerationSelection(ProtocolModel):
    mode: Literal["explicit"] = "explicit"
    value: ReasoningEffort


ModelChoice = Annotated[
    InheritSelection | ModelDefaultSelection | ExplicitModelSelection,
    Field(discriminator="mode"),
]
GenerationChoice = Annotated[
    InheritSelection | ModelDefaultSelection | ExplicitGenerationSelection,
    Field(discriminator="mode"),
]


class SelectionChoice(ProtocolModel):
    """One bounded selection payload carrying both independent choices."""

    model: ModelChoice | None = None
    generation: GenerationChoice | None = None

    @model_validator(mode="after")
    def bounded(self):
        if self.model is None and self.generation is None:
            raise ValueError("selection must carry a model or generation choice")
        return self


@dataclass(frozen=True)
class ResolvedSelection:
    """Resolved model and generation values with per-value source markers."""

    model: ModelRef | None
    model_source: SelectionSource
    generation: ReasoningEffort | None
    generation_source: SelectionSource
    status: Literal["ok", "needs_default", "invalid"]
    diagnostics: tuple[str, ...] = ()

    def summary(self) -> dict:
        """Bounded wire projection: values plus sources plus stable diagnostics."""

        return {
            "model": self.model.model_dump(mode="json") if self.model is not None else None,
            "model_source": self.model_source,
            "generation": self.generation,
            "generation_source": self.generation_source,
            "status": self.status,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class SelectionInputs:
    """Independent per-level choices feeding the D06 resolution chain.

    ``agent_model`` keeps the existing definition-level
    ``ModelRef | "invoking_active"`` shape. Session-snapshot generations use the
    existing ``GenerationOptions`` shape: an effort value is the inherited
    explicit value and an empty object is the explicit adapter-default choice.
    """

    node_model: ModelChoice | None = None
    node_generation: GenerationChoice | None = None
    agent_model: ModelRef | Literal["invoking_active"] | None = None
    agent_generation: GenerationChoice | None = None
    session_model: ModelRef | None = None
    session_generation: GenerationOptions | None = None
    adapter_default_model: ModelRef | None = None


def resolve_selection(
    inputs: SelectionInputs,
    *,
    exact_reasoning_efforts: tuple[ReasoningEffort, ...] | None = None,
    model_available=None,
) -> ResolvedSelection:
    """Resolve model and generation independently; never silently drop values.

    ``exact_reasoning_efforts`` lists the exact Model's supported efforts;
    ``None`` means the provider support data is unavailable and keeps the
    selection unresolved.
    ``model_available`` is an optional predicate for explicit ModelRefs
    (provider catalog membership). An explicit unsupported effort is rejected;
    an inherited incompatible effort returns ``needs_default`` with the model
    default as the proposed fallback.
    """
    diagnostics: list[str] = []
    status: Literal["ok", "needs_default", "invalid"] = "ok"

    model, model_source = _resolve_model(inputs, model_available, diagnostics)
    if "model_unavailable" in diagnostics:
        status = "invalid"

    generation, generation_source, explicit = _resolve_generation(inputs)
    if generation is not None and exact_reasoning_efforts is not None:
        if generation not in exact_reasoning_efforts:
            if explicit:
                # An explicit effort the exact Model cannot express is a hard
                # rejection; it must not be dropped or quietly replaced.
                diagnostics.append("generation_explicit_unsupported")
                status = "invalid"
            else:
                # Inherited from the session snapshot: surface the decision
                # instead of keeping an unusable value; the adapter default is
                # only the proposed fallback until the user confirms one.
                diagnostics.append("generation_inherit_unsupported:choose_default")
                if status == "ok":
                    status = "needs_default"
                generation = None
                generation_source = "adapter_default"

    return ResolvedSelection(
        model=model,
        model_source=model_source,
        generation=generation,
        generation_source=generation_source,
        status=status,
        diagnostics=tuple(diagnostics),
    )


def _resolve_model(inputs: SelectionInputs, model_available, diagnostics):
    def unavailable(source):
        diagnostics.append("model_unavailable")
        return None, source

    choice = inputs.node_model
    if choice is not None:
        if choice.mode == "explicit":
            if model_available is not None and not model_available(choice.value):
                return unavailable("node")
            return choice.value, "node"
        if choice.mode == "model_default":
            return inputs.adapter_default_model, "node"
    if isinstance(inputs.agent_model, ModelRef):
        if model_available is not None and not model_available(inputs.agent_model):
            return unavailable("agent")
        return inputs.agent_model, "agent"
    if inputs.session_model is not None:
        if model_available is not None and not model_available(inputs.session_model):
            return unavailable("session_snapshot")
        return inputs.session_model, "session_snapshot"
    return inputs.adapter_default_model, "adapter_default"


def _resolve_generation(inputs: SelectionInputs):
    for source, choice in (
        ("node", inputs.node_generation),
        ("agent", inputs.agent_generation),
    ):
        if choice is None:
            continue
        if choice.mode == "explicit":
            return choice.value, source, True
        if choice.mode == "model_default":
            return None, source, True
    if inputs.session_generation is not None:
        if inputs.session_generation.reasoning_effort is not None:
            return inputs.session_generation.reasoning_effort, "session_snapshot", False
        return None, "session_snapshot", False
    return None, "adapter_default", False

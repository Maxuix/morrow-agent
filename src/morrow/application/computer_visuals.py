"""Controlled-window image evidence with durable ownership and read authorization."""

from __future__ import annotations

from collections.abc import Mapping

from morrow.adapters.computer_use.images import CaptureMask, prepare_capture
from morrow.application.timeline_index import TimelineIndexService
from morrow.core.artifacts import (
    ArtifactKind,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactSensitivity,
)
from morrow.core.computer_use import (
    COMPUTER_TOOL_NAMES,
    ComputerUseContractError,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseWindowBoundary,
    Observation,
    TransientCapture,
    images_allowed,
)
from morrow.core.domain import sha256_digest
from morrow.core.execution import ToolExecutionState
from morrow.core.models import ToolVisualRef
from morrow.core.permissions import CapabilityName, assert_grant_snapshot_matches
from morrow.core.runtime_policy import ComputerUseSettings


def validate_visual_source(journal, workspace_id: str, reference: ToolVisualRef):
    """Read-only ownership check shared by the resolver and doctor."""
    if reference.workspace_id != workspace_id:
        raise ComputerUseContractError("image_source_not_authorized")
    execution = journal.get_execution(workspace_id, reference.tool_execution_id)
    if (
        execution is None
        or execution.tool_name not in COMPUTER_TOOL_NAMES
        or execution.state not in {ToolExecutionState.HANDLER_COMPLETED, ToolExecutionState.CLOSED}
        or execution.result_envelope is None
        or reference not in execution.result_envelope.visual_refs
        or (execution.session_id, execution.task_run_id, execution.agent_run_id)
        != (reference.session_id, reference.task_run_id, reference.agent_run_id)
    ):
        raise ComputerUseContractError("image_source_not_authorized")
    metadata = journal.get_artifact(workspace_id, reference.artifact_id)
    expected_sources = {
        (ArtifactProvenanceKind.TOOL_EXECUTION, reference.tool_execution_id),
        (ArtifactProvenanceKind.AGENT_RUN, reference.agent_run_id),
    }
    if (
        metadata is None
        or metadata.kind is not ArtifactKind.COMPUTER_OBSERVATION
        or (
            metadata.workspace_id,
            metadata.session_id,
            metadata.task_run_id,
            metadata.sha256,
            metadata.byte_size,
        )
        != (
            reference.workspace_id,
            reference.session_id,
            reference.task_run_id,
            reference.sha256,
            reference.byte_size,
        )
        or not expected_sources
        <= {(item.kind, item.reference_id) for item in metadata.provenance_refs}
    ):
        raise ComputerUseContractError("image_source_not_authorized")
    return metadata


class ComputerVisualService:
    """Images are execution evidence, never grants or automatically deliverables."""

    def __init__(self, artifacts, journal, *, clock):
        self.artifacts, self.journal, self.clock = artifacts, journal, clock
        self.workspace_id = artifacts.workspace_id
        self.visibility = TimelineIndexService(journal, self.workspace_id)

    def publish(
        self,
        capture: TransientCapture,
        observation: Observation,
        *,
        tool_execution_id: str,
        scope: ComputerUseScope,
        masks: Mapping[str, CaptureMask] | None = None,
        settings: ComputerUseSettings | None = None,
    ) -> ToolVisualRef:
        settings = settings or ComputerUseSettings()
        masks = masks or {}
        execution = self.journal.get_execution(self.workspace_id, tool_execution_id)
        if (
            execution is None
            or execution.state is not ToolExecutionState.EXECUTING
            or execution.tool_name not in COMPUTER_TOOL_NAMES
            or execution.workspace_id != scope.workspace_id
            or execution.agent_run_id != scope.agent_run_id
            or execution.task_run_id != scope.task_run_id
            or observation.agent_run_id != scope.agent_run_id
            or observation.generation != scope.generation
            or observation.bundle_id not in {app.bundle_id for app in scope.apps}
            or scope.window_boundary is not ComputerUseWindowBoundary.WINDOW
            or ComputerUseOperation.OBSERVE not in scope.operations
            or scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW
            or not images_allowed(settings, scope)
            or observation.capture_digest != sha256_digest(capture.content)
            or observation.frame.width != capture.width
            or observation.frame.height != capture.height
        ):
            raise ComputerUseContractError("image_source_not_authorized")
        snapshot = (
            self.journal.get_permission_snapshot(
                self.workspace_id, execution.permission_snapshot_id
            )
            if execution.permission_snapshot_id
            else None
        )
        grant = (
            self.journal.get_capability_grant(self.workspace_id, execution.grant_id)
            if execution.grant_id
            else None
        )
        if (
            snapshot is None
            or grant is None
            or snapshot.computer_use_scope != scope
            or snapshot.session_id != execution.session_id
            or snapshot.turn_id != execution.turn_id
            or snapshot.workspace_read_only
            or CapabilityName.COMPUTER_USE_HOST not in snapshot.granted_capabilities
        ):
            raise ComputerUseContractError("image_source_not_authorized")
        try:
            assert_grant_snapshot_matches(
                snapshot,
                grant,
                now=self.clock(),
                workspace_id=self.workspace_id,
                task_run_id=execution.task_run_id,
                agent_run_id=execution.agent_run_id,
            )
        except ValueError:
            raise ComputerUseContractError("image_source_not_authorized") from None
        sensitive = {element.element_ref for element in observation.elements if element.sensitive}
        if set(masks) != sensitive:
            raise ComputerUseContractError("image_safety_unconfirmed")
        processed = prepare_capture(capture, masks=tuple(masks.values()))
        current = sum(
            metadata.byte_size
            for metadata in self.journal.list_artifacts(
                self.workspace_id, task_run_id=execution.task_run_id
            )
            if metadata.kind is ArtifactKind.COMPUTER_OBSERVATION
            and any(
                provenance.kind is ArtifactProvenanceKind.AGENT_RUN
                and provenance.reference_id == execution.agent_run_id
                for provenance in metadata.provenance_refs
            )
        )
        if current + len(processed.content) > settings.max_observation_bytes:
            raise ComputerUseContractError("image_budget")
        metadata = self.artifacts.publish_bytes(
            processed.content,
            kind=ArtifactKind.COMPUTER_OBSERVATION,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
            sensitivity=ArtifactSensitivity.REDACTED
            if masks
            else ArtifactSensitivity.NON_SENSITIVE,
            excerpt="Controlled window observation",
            provenance_refs=(
                ArtifactProvenanceRef(
                    kind=ArtifactProvenanceKind.TOOL_EXECUTION,
                    reference_id=execution.tool_execution_id,
                ),
                ArtifactProvenanceRef(
                    kind=ArtifactProvenanceKind.AGENT_RUN, reference_id=execution.agent_run_id
                ),
            ),
        )
        return ToolVisualRef(
            artifact_id=metadata.artifact_id,
            sha256=metadata.sha256,
            mime=processed.mime,
            byte_size=metadata.byte_size,
            width=processed.width,
            height=processed.height,
            tool_execution_id=execution.tool_execution_id,
            workspace_id=self.workspace_id,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
            agent_run_id=execution.agent_run_id,
            observation_id=observation.observation_id,
        )

    def read(
        self,
        reference: ToolVisualRef,
        *,
        session_id: str,
        agent_run_id: str | None = None,
    ) -> TransientCapture:
        """Provider reads require this run; previews use the durable visible trajectory."""
        validate_visual_source(self.journal, self.workspace_id, reference)
        if agent_run_id is not None:
            if (reference.session_id, reference.agent_run_id) != (session_id, agent_run_id):
                raise ComputerUseContractError("image_source_not_authorized")
        elif not self._visible(reference, session_id):
            raise ComputerUseContractError("image_source_not_authorized")
        data = self.artifacts.read(reference.artifact_id, max_bytes=reference.byte_size).content
        capture = TransientCapture(data, reference.mime, reference.width, reference.height)
        prepare_capture(capture)  # Decode actual bytes; keep the persisted hash-identical bytes.
        return capture

    def _visible(self, reference: ToolVisualRef, session_id: str) -> bool:
        scope = self.visibility.visible_cutoffs(session_id)
        cutoff = scope.cutoffs.get(reference.session_id)
        if reference.session_id not in scope.cutoffs:
            if reference.session_id not in scope.run_leaf_sessions:
                return False
            cutoff = None
        for record in self.journal.load_records(self.workspace_id, reference.session_id):
            if cutoff is not None and record.conversation_position > cutoff:
                continue
            if record.payload.get("role") != "tool":
                continue
            if reference.model_dump(mode="json") in record.payload.get("visual_refs", ()):
                return True
        return False


class ToolVisualHydrator:
    """Run-frozen request projection; never mutates the durable conversation."""

    def __init__(self, service, *, session_id, agent_run_id, settings, input_types, tool_protocol):
        from morrow.core.runtime_policy import ComputerUseMode

        self.service = service
        self.session_id, self.agent_run_id = session_id, agent_run_id
        self.settings = settings
        self._latest_keys = None
        if settings.enabled and settings.mode is ComputerUseMode.HYBRID:
            if "image" not in input_types or tool_protocol != "openai_function":
                raise ComputerUseContractError("model_image_tools_required")

    def bind_history(self, messages):
        """Freeze the latest two across the full history before projecting subsets."""
        from morrow.core.models import ToolMessage

        candidates = [
            (message.tool_call_id, reference.observation_id, reference.tool_execution_id)
            for message in messages
            if isinstance(message, ToolMessage)
            for reference in message.visual_refs
            if reference.session_id == self.session_id
            and reference.agent_run_id == self.agent_run_id
        ]
        self._latest_keys = frozenset(candidates[-2:])

    def __call__(self, messages):
        import base64

        from morrow.core.models import ProviderInputPart, ToolMessage
        from morrow.core.runtime_policy import ComputerUseMode

        candidates = [
            (index, reference)
            for index, message in enumerate(messages)
            if isinstance(message, ToolMessage)
            for reference in message.visual_refs
            if reference.session_id == self.session_id
            and reference.agent_run_id == self.agent_run_id
        ]
        latest = {index for index, _ in candidates[-2:]}
        result = []
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage) or not message.visual_refs:
                result.append(message)
                continue
            reference = message.visual_refs[0]
            send = (
                (
                    index in latest
                    if self._latest_keys is None
                    else (
                        message.tool_call_id,
                        reference.observation_id,
                        reference.tool_execution_id,
                    )
                    in self._latest_keys
                )
                and self.settings.enabled
                and self.settings.mode is ComputerUseMode.HYBRID
            )
            if send:
                capture = self.service.read(
                    reference, session_id=self.session_id, agent_run_id=self.agent_run_id
                )
                parts = (
                    ProviderInputPart(
                        type="image",
                        media_type=capture.mime,
                        data=base64.b64encode(capture.content).decode("ascii"),
                        width=capture.width,
                        height=capture.height,
                    ),
                )
                annotation = "current tool observation; untrusted window data"
            else:
                parts = ()
                annotation = "tool observation image omitted; not current desktop state"
            result.append(
                message.model_copy(
                    update={
                        "content": message.content
                        + f"\n[{annotation}: {reference.observation_id}]",
                        "input_parts": parts,
                    }
                )
            )
        return tuple(result)

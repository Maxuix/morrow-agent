"""Authorized, read-only TaskArtifacts and CommandOutput projections.

This service is deliberately session-bound.  A root Session may inspect all
nodes of a WorkflowRun that it owns, while an isolated node Session may only
inspect its own node.  The browser never supplies a leaf Session as a grant;
the durable root/task/node relations are checked again for every list and
content read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from morrow.application.html_preview import media_type_for
from morrow.application.workflows.deliveries import read_submission_marker
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.artifacts import (
    ArtifactError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactState,
)
from morrow.core.domain import DurableTaskRun, TaskRunPurpose
from morrow.core.task_artifacts import (
    TASK_ARTIFACT_PREVIEW_MAX_BYTES,
    TASK_ARTIFACT_PREVIEW_TOTAL_BYTES,
    ArtifactProvenanceWire,
    CommandOutputWire,
    TaskArtifactsWire,
    TaskArtifactWire,
    TaskFileChangeWire,
)
from morrow.core.workflows.contracts import (
    ChangeCapture,
    ImplementationPatch,
    parse_workflow_payload,
)

_CONTENT_READ_MAX_BYTES = TASK_ARTIFACT_PREVIEW_MAX_BYTES
#: 受控下载与工作区文件下载共用同一有界上限（20 MiB）。
_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024
_COMMAND_TOOLS = frozenset({"bash", "run_command"})
_DIFF_KINDS = frozenset({ArtifactKind.PATCH, ArtifactKind.DIFF})
_IMAGE_SUFFIXES = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico"})
_TEXT_MEDIA_PREFIXES = ("text/", "application/json", "application/javascript", "image/svg+xml")


def _suffix_of(name: str) -> str:
    leaf = name.rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""


def _preview_family(name: str, mime: str) -> str:
    """How the returned bytes should be shown; never inferred from a failed decode."""

    suffix = _suffix_of(name)
    if suffix in _IMAGE_SUFFIXES or mime.startswith("image/"):
        return "image"
    if suffix == "pdf" or mime == "application/pdf":
        return "pdf"
    if suffix in {"html", "htm"} or mime.startswith("text/html"):
        return "html"
    if mime.startswith(_TEXT_MEDIA_PREFIXES):
        return "text"
    return "binary"


def _decode_bounded(content: bytes) -> tuple[str, bool, bool]:
    """(text, cut_tail, is_text) for one already bounded read.

    A multi-byte character split by the read budget is dropped and reported as
    truncation; it never becomes a replacement character or a binary verdict.
    """

    if not content:
        return "", False, True
    if b"\x00" in content[:4096]:
        return "", False, False
    try:
        return content.decode("utf-8"), False, True
    except UnicodeDecodeError as exc:
        if exc.end == len(content) and exc.reason == "unexpected end of data":
            try:
                return content[: exc.start].decode("utf-8"), True, True
            except UnicodeDecodeError:
                return "", False, False
        return "", False, False


_REPORT_KINDS = frozenset(
    {
        ArtifactKind.PATCH,
        ArtifactKind.DIFF,
        ArtifactKind.TEST_REPORT,
        ArtifactKind.DIAGNOSTIC_REPORT,
        ArtifactKind.TASK_SUMMARY,
        ArtifactKind.CONTEXT_SUMMARY,
    }
)


@dataclass(frozen=True)
class _Scope:
    session_id: str
    task: DurableTaskRun | None
    runs: tuple[Any, ...]
    nodes: tuple[Any, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.task is not None:
            values.append(self.task.task_run_id)
        for node in self.nodes:
            if node.leaf_task_run_id and node.leaf_task_run_id not in values:
                values.append(node.leaf_task_run_id)
        return tuple(values)


@dataclass(frozen=True)
class _ArtifactRead:
    content: bytes | None
    availability: str


class TaskArtifactsService:
    """Build the shared TaskArtifacts/CommandOutput read model."""

    def __init__(self, journal, *, artifacts, workflow_queries, workspace_id: str) -> None:
        self.journal = journal
        self.artifacts = artifacts
        self.workflow_queries = workflow_queries
        self.workspace_id = workspace_id

    def view(
        self,
        session_id: str,
        *,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        node_run_id: str | None = None,
    ) -> TaskArtifactsWire:
        scope = self._resolve(
            session_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
        )
        return self._build(scope)

    def authorized_artifacts_for_task(
        self, session_id: str, task_run_id: str
    ) -> dict[str, ArtifactMetadata]:
        """The authorized Artifact set for one explicit historical task.

        Chat-result projection reads a durable Outcome's own task, never the
        caller's current selection, so a historical result can never be widened
        by whatever task happens to be selected now. Scope resolution and
        ownership checks stay exactly those of :meth:`view`.
        """

        scope = self._resolve(
            session_id, task_run_id=task_run_id, workflow_run_id=None, node_run_id=None
        )
        return self._authorized_artifacts(scope)

    def read_content(
        self,
        session_id: str,
        artifact_id: str,
        *,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        node_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one bounded Artifact only after rebuilding its auth scope."""

        scope = self._resolve(
            session_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
        )
        allowed = self._authorized_artifacts(scope)
        metadata = allowed.get(artifact_id)
        if metadata is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Artifact is outside this Task scope"
            )
        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            )
        try:
            result = self.artifacts.read(artifact_id, max_bytes=_CONTENT_READ_MAX_BYTES)
        except ArtifactError:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            ) from None
        text, cut_tail, is_text = _decode_bounded(result.content)
        described = self._content_metadata(scope, metadata, is_text=is_text)
        return {
            # The optional fields describe exactly the bytes returned here; the
            # full bytes stay behind download().
            "content": text if is_text else None,
            "truncated": metadata.byte_size > len(result.content) or cut_tail,
            "byte_size": metadata.byte_size,
            "encoding": "utf8" if is_text else "binary",
            "content_kind": "text" if is_text else "binary",
            **described,
        }

    def download(
        self,
        session_id: str,
        artifact_id: str,
        *,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        node_run_id: str | None = None,
    ) -> tuple[bytes, str, str]:
        """Full raw bytes with the real file name and media type.

        Scope validation is exactly :meth:`read_content`; only the delivery
        path knows a real name, so anything else falls back to the Artifact
        identity rather than inventing one.
        """

        scope = self._resolve(
            session_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
        )
        allowed = self._authorized_artifacts(scope)
        metadata = allowed.get(artifact_id)
        if metadata is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Artifact is outside this Task scope"
            )
        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            )
        if metadata.byte_size > _DOWNLOAD_MAX_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Artifact 超过 20 MiB 下载上限")
        try:
            content = self.artifacts.read(artifact_id, max_bytes=_DOWNLOAD_MAX_BYTES).content
        except ArtifactError:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            ) from None
        name, _path, mime = self._artifact_names(scope, metadata)
        return content, name, mime

    def read_bytes(
        self,
        session_id: str,
        artifact_id: str,
        *,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        node_run_id: str | None = None,
    ) -> bytes:
        """Artifact 的受控下载：范围与 :meth:`read_content` 完全一致。"""

        scope = self._resolve(
            session_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
        )
        allowed = self._authorized_artifacts(scope)
        metadata = allowed.get(artifact_id)
        if metadata is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Artifact is outside this Task scope"
            )
        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            )
        if metadata.byte_size > _DOWNLOAD_MAX_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Artifact 超过 20 MiB 下载上限")
        try:
            return self.artifacts.read(artifact_id, max_bytes=_DOWNLOAD_MAX_BYTES).content
        except ArtifactError:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            ) from None

    # Authorization ---------------------------------------------------------

    def _resolve(
        self,
        session_id: str,
        *,
        task_run_id: str | None,
        workflow_run_id: str | None,
        node_run_id: str | None,
    ) -> _Scope:
        session = self.journal.get_session(self.workspace_id, session_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        if node_run_id is not None:
            node = self.journal.workflows.get_node(self.workspace_id, node_run_id)
            if node is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow node is missing")
            run = self.journal.workflows.get_run(self.workspace_id, node.workflow_run_id)
            if run is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow run is missing")
            self._require_run_selector(run, workflow_run_id)
            root = self._root_task(run)
            root_owned = root is not None and root.session_id == session_id
            node_owned = node.conversation_session_id == session_id
            if not root_owned and not node_owned:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Workflow node is outside this Session"
                )
            selected_task = root if root_owned else self._leaf_task(node)
            if not root_owned and selected_task is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND,
                    "Workflow node task ownership is missing",
                )
            self._require_task_selector(selected_task, task_run_id)
            # A root may inspect this one explicitly selected node; a leaf may
            # never widen the selection to its siblings.
            return _Scope(session_id, selected_task, (run,), (node,))

        if workflow_run_id is not None:
            run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
            if run is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow run is missing")
            root = self._root_task(run)
            if root is not None and root.session_id == session_id:
                nodes = self._nodes_for_run(run.workflow_run_id)
                selected_task = root
            else:
                nodes = tuple(
                    node
                    for node in self._nodes_for_run(run.workflow_run_id)
                    if node.conversation_session_id == session_id
                )
                if not nodes:
                    raise ApplicationError(
                        ApplicationErrorCode.NOT_FOUND, "Workflow is outside this Session"
                    )
                if task_run_id is not None:
                    selected_node = next(
                        (node for node in nodes if node.leaf_task_run_id == task_run_id),
                        None,
                    )
                    if selected_node is None:
                        raise ApplicationError(
                            ApplicationErrorCode.NOT_FOUND,
                            "Task selector does not match the Workflow node Session",
                        )
                    nodes = (selected_node,)
                selected_task = self._leaf_task(nodes[0])
            self._require_task_selector(selected_task, task_run_id)
            return _Scope(session_id, selected_task, (run,), nodes)

        if task_run_id is not None:
            task = self.journal.get_task_run(self.workspace_id, task_run_id)
            if task is None or task.session_id != session_id:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Task is outside this Session"
                )
            if task.purpose is TaskRunPurpose.WORKFLOW_NODE:
                nodes = tuple(
                    node
                    for node in self.journal.workflows.nodes_for_conversation_session(
                        self.workspace_id, session_id
                    )
                    if node.leaf_task_run_id == task_run_id
                )
                if not nodes:
                    raise ApplicationError(
                        ApplicationErrorCode.NOT_FOUND, "Task node ownership is missing"
                    )
                runs = self._unique_runs(nodes)
                return _Scope(session_id, task, runs, nodes)
            runs = tuple(
                run
                for run in self.journal.workflows.runs_for_root_session(
                    self.workspace_id, session_id
                )
                if run.root_task_run_id == task_run_id
            )
            nodes = tuple(node for run in runs for node in self._nodes_for_run(run.workflow_run_id))
            return _Scope(session_id, task, runs, nodes)

        selected = self._default_task(session_id)
        if selected is None:
            return _Scope(session_id, None, (), ())
        return self._resolve(
            session_id, task_run_id=selected.task_run_id, workflow_run_id=None, node_run_id=None
        )

    def _default_task(self, session_id: str) -> DurableTaskRun | None:
        session = self.journal.get_session(self.workspace_id, session_id)
        current = session.current_task_run_id if session is not None else None
        if current:
            task = self.journal.get_task_run(self.workspace_id, current)
            if task is not None and task.session_id == session_id:
                return task
        tasks = tuple(
            task
            for task in self.journal.list_task_runs(self.workspace_id, session_id)
            if task.purpose is TaskRunPurpose.USER
        )
        return max(tasks, key=lambda item: (item.updated_at, item.task_run_id), default=None)

    def _root_task(self, run):
        task = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        if task is None or task.purpose is not TaskRunPurpose.USER:
            return None
        return task

    def _leaf_task(self, node):
        if node.leaf_task_run_id is None:
            return None
        task = self.journal.get_task_run(self.workspace_id, node.leaf_task_run_id)
        if task is None or task.purpose is not TaskRunPurpose.WORKFLOW_NODE:
            return None
        if task.session_id != node.conversation_session_id:
            return None
        return task

    def _nodes_for_run(self, run_id: str):
        return tuple(
            node
            for node in self.journal.workflows.list_nodes(self.workspace_id, run_id)
            if (
                node is not None
                and node.workflow_run_id == run_id
                and self._leaf_task(node) is not None
            )
        )

    def _unique_runs(self, nodes):
        values = []
        seen = set()
        for node in nodes:
            if node.workflow_run_id in seen:
                continue
            run = self.journal.workflows.get_run(self.workspace_id, node.workflow_run_id)
            if run is not None:
                values.append(run)
                seen.add(node.workflow_run_id)
        return tuple(values)

    @staticmethod
    def _require_run_selector(run, selected: str | None) -> None:
        if selected is not None and run.workflow_run_id != selected:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Workflow selector does not match the node"
            )

    @staticmethod
    def _require_task_selector(task, selected: str | None) -> None:
        if selected is not None and (task is None or task.task_run_id != selected):
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Task selector does not match the scope"
            )

    # Projection -------------------------------------------------------------

    def _build(self, scope: _Scope) -> TaskArtifactsWire:
        selected_task = scope.task
        title = self._title(scope)
        task_wire = self._task_wire(selected_task) if selected_task is not None else None
        outcomes = self._outcomes(scope.task_ids)
        metadata = self._authorized_artifacts(scope)

        read_budget = TASK_ARTIFACT_PREVIEW_TOTAL_BYTES
        artifact_entries: list[TaskArtifactWire] = []
        for item in sorted(
            metadata.values(), key=lambda value: (value.created_at, value.artifact_id)
        ):
            entry, payload, used = self._artifact_entry(item, read_budget, scope)
            read_budget = max(0, read_budget - used)
            artifact_entries.append(entry)

        files = self._file_changes(scope, metadata, artifact_entries, outcomes)
        commands = self._command_outputs(scope, metadata)
        availability = self._overall_availability(artifact_entries, files, commands)
        workflow_ids = tuple(run.workflow_run_id for run in scope.runs)
        primary_workflow = workflow_ids[-1] if workflow_ids else None
        primary_node = scope.nodes[0].node_run_id if len(scope.nodes) == 1 else None
        message = None
        if selected_task is None:
            message = "此会话还没有可显示的任务产物。"
        elif not artifact_entries and not files and not commands and not outcomes:
            message = "该历史任务没有登记产物或命令输出。"
        return TaskArtifactsWire(
            session_id=scope.session_id,
            task_run_id=selected_task.task_run_id if selected_task is not None else None,
            title=title,
            status=selected_task.status.value if selected_task is not None else None,
            purpose=selected_task.purpose.value if selected_task is not None else None,
            workflow_run_id=primary_workflow,
            workflow_run_ids=workflow_ids,
            node_run_id=primary_node,
            task=task_wire,
            outcomes=tuple(outcomes),
            artifacts=tuple(artifact_entries),
            files=tuple(files),
            command_outputs=tuple(commands),
            availability=availability,
            message=message,
        )

    def _title(self, scope: _Scope) -> str:
        selected = scope.task
        if (
            selected is not None
            and selected.purpose is TaskRunPurpose.WORKFLOW_NODE
            and scope.nodes
        ):
            objective = self._node_objective(scope.nodes[0])
            if objective:
                return objective[:256]
        if selected is not None:
            title = self._session_title(selected.session_id)
            return title[:256]
        return self._session_title(scope.session_id)[:256]

    def _session_title(self, session_id: str) -> str:
        metadata = getattr(self.journal, "session_metadata", None)
        if metadata is not None:
            try:
                value = metadata.get(self.workspace_id, session_id)
                title = value.get("title") if isinstance(value, dict) else None
                if isinstance(title, str) and title.strip():
                    return " ".join(title.split())
            except Exception:
                pass
        return "当前会话"

    def _node_objective(self, node) -> str | None:
        try:
            run = self.journal.workflows.get_run(self.workspace_id, node.workflow_run_id)
            revision = self.journal.workflows.get_revision(
                self.workspace_id, run.workflow_revision_id
            )
            definition = next(item for item in revision.nodes if item.node_id == node.node_id)
            objective = getattr(getattr(definition, "task_contract", None), "objective", None)
            return objective if isinstance(objective, str) and objective.strip() else None
        except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
            return None

    def _task_wire(self, task: DurableTaskRun) -> dict[str, Any]:
        return {
            "task_run_id": task.task_run_id,
            "session_id": task.session_id,
            "purpose": task.purpose.value,
            "status": task.status.value,
            "attempt": task.attempt,
            "row_version": task.row_version,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "accepted_at": task.accepted_at.isoformat() if task.accepted_at else None,
            "closed_at": task.closed_at.isoformat() if task.closed_at else None,
        }

    def _outcomes(self, task_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        values = []
        seen = set()
        for task_id in task_ids:
            for outcome in self.journal.list_task_outcomes(self.workspace_id, task_id):
                if outcome.outcome_id in seen:
                    continue
                seen.add(outcome.outcome_id)
                values.append(
                    {
                        "outcome_id": outcome.outcome_id,
                        "task_run_id": outcome.task_run_id,
                        "session_id": outcome.session_id,
                        "version": outcome.version,
                        "task_status": outcome.task_status.value,
                        "summary": outcome.summary,
                        "trigger": outcome.trigger.value,
                        "completion_basis": list(outcome.completion_basis),
                        "changed_paths": list(outcome.changed_paths),
                        "side_effects": list(outcome.side_effects),
                        "unresolved_items": list(outcome.unresolved_items),
                        "feedback": list(outcome.feedback),
                        "artifact_refs": [
                            ref.model_dump(mode="json") for ref in outcome.artifact_refs
                        ],
                        "evidence_refs": [
                            ref.model_dump(mode="json") for ref in outcome.evidence_refs
                        ],
                        "created_at": outcome.created_at.isoformat(),
                    }
                )
        return sorted(values, key=lambda item: (item["created_at"], item["outcome_id"]))

    def _authorized_artifacts(self, scope: _Scope) -> dict[str, ArtifactMetadata]:
        values: dict[str, ArtifactMetadata] = {}
        for task_id in scope.task_ids:
            node = next(
                (item for item in scope.nodes if item.leaf_task_run_id == task_id),
                None,
            )
            expected_session = (
                node.conversation_session_id if node is not None else scope.session_id
            )
            for item in self.journal.list_artifacts(self.workspace_id, task_run_id=task_id):
                if (
                    item.workspace_id != self.workspace_id
                    or item.task_run_id != task_id
                    or item.session_id != expected_session
                ):
                    continue
                values[item.artifact_id] = item
        # Effective outputs are added separately in _workflow_outputs.  This
        # method is also the content-read allowlist, so it must include every
        # node output reachable through the already authorized scope.
        values.update(self._workflow_outputs(scope))
        return values

    def _workflow_outputs(self, scope: _Scope) -> dict[str, ArtifactMetadata]:
        values: dict[str, ArtifactMetadata] = {}
        if self.workflow_queries is None:
            return values
        allowed_nodes = {node.node_run_id for node in scope.nodes}
        nodes_by_id = {node.node_run_id: node for node in scope.nodes}
        for run in scope.runs:
            view = self.workflow_queries.get_run_view(run.workflow_run_id)
            if view is None:
                continue
            for node_view in view.nodes:
                if node_view.node.node_run_id not in allowed_nodes:
                    continue
                for item in node_view.artifacts:
                    if self._artifact_belongs_to_node(item, node_view.node):
                        values[item.artifact_id] = item
                # Registered delivery snapshots never occupy the node contract
                # columns; they are authorized through the verified marker of
                # this already in-scope node run.
                self._add_node_delivery_snapshots(values, node_view.node)
            # For inherited/effective outputs, verify the output belongs to a
            # node in the scope before adding it.  This is the root -> run ->
            # node -> source session -> Artifact authorization chain.
            for output in view.effective_outputs:
                if output.artifact is None:
                    continue
                node = nodes_by_id.get(output.artifact.producer_node_run_id)
                if node is not None:
                    if self._artifact_belongs_to_node(output.artifact, node):
                        values[output.artifact.artifact_id] = output.artifact
                    continue
                if output.inherited:
                    self._add_authorized_inherited_output(values, scope, run, view, output)
        return values

    def _add_authorized_inherited_output(self, values, scope, run, view, output) -> None:
        import_row = next(
            (
                item
                for item in view.inherited_artifacts
                if item.workflow_run_id == run.workflow_run_id
                and item.source_node_id == output.node_id
                and item.output_slot == output.output_slot
                and item.artifact_id == output.artifact.artifact_id
            ),
            None,
        )
        if import_row is None:
            return
        source_run = self.journal.workflows.get_run(
            self.workspace_id, import_row.source_workflow_run_id
        )
        source_node = self.journal.workflows.get_node(
            self.workspace_id, import_row.source_node_run_id
        )
        source_artifact = self.journal.get_artifact(self.workspace_id, import_row.artifact_id)
        if (
            source_run is None
            or source_node is None
            or source_artifact is None
            or source_node.workflow_run_id != source_run.workflow_run_id
            or not self._artifact_belongs_to_node(source_artifact, source_node)
        ):
            return
        source_root = self._root_task(source_run)
        root_owned = (
            scope.task is not None
            and scope.task.purpose is TaskRunPurpose.USER
            and source_root is not None
            and source_root.session_id == scope.session_id
            and source_root.task_run_id == scope.task.task_run_id
        )
        leaf_owned = source_node.conversation_session_id == scope.session_id
        if root_owned or leaf_owned:
            values[source_artifact.artifact_id] = source_artifact
            # A continuation exports the source node's registered deliveries
            # too; the marker edge is re-verified against the source node.
            marker = read_submission_marker(
                self.artifacts, node_run_id=import_row.source_node_run_id
            )
            self._add_slot_delivery_snapshots(
                values, marker, slot=output.output_slot, node=source_node
            )

    def _add_node_delivery_snapshots(self, values, node) -> None:
        """Every delivery snapshot one already authorized node run registered."""

        marker = read_submission_marker(self.artifacts, node_run_id=node.node_run_id)
        if marker is None:
            return
        for descriptor in marker.deliverables:
            self._add_delivery_snapshot(values, marker, descriptor, node)

    def _add_slot_delivery_snapshots(self, values, marker, *, slot: str, node) -> None:
        if marker is None:
            return
        for descriptor in marker.deliverables:
            if descriptor.slot == slot:
                self._add_delivery_snapshot(values, marker, descriptor, node)

    def _add_delivery_snapshot(self, values, marker, descriptor, node) -> None:
        identities = (
            descriptor.artifact_id,
            *(item.artifact_id for item in descriptor.resources),
        )
        for artifact_id in identities:
            item = self.journal.get_artifact(self.workspace_id, artifact_id)
            if item is None or not self._delivery_belongs_to_node(item, node, marker):
                continue
            values[artifact_id] = item

    def _delivery_belongs_to_node(self, item: ArtifactMetadata, node, marker) -> bool:
        """A marker may only name snapshots the node itself really published."""

        if self.artifacts is None or item.kind is not ArtifactKind.DELIVERABLE:
            return False
        if item.workspace_id != self.workspace_id:
            return False
        if item.producer_node_run_id is not None or item.output_slot is not None:
            return False
        if self._leaf_task(node) is None:
            return False
        if item.session_id != node.conversation_session_id:
            return False
        if item.task_run_id != node.leaf_task_run_id:
            return False
        if marker.tool_execution_id is None:
            return True
        return any(
            ref.kind.value == "tool_execution"
            and ref.reference_id == marker.tool_execution_id
            and ref.role == "delivery_submission"
            for ref in item.provenance_refs
        )

    def _artifact_names(
        self, scope: _Scope, metadata: ArtifactMetadata
    ) -> tuple[str, str | None, str]:
        """The most specific name/path/MIME this scope can prove for one Artifact."""

        delivery = self._delivery_names(scope, metadata.artifact_id)
        if delivery is not None:
            return delivery
        if metadata.kind is ArtifactKind.DELIVERABLE:
            # A delivery snapshot whose marker is not in scope never claims a
            # name or a text type it cannot prove.
            return metadata.artifact_id, None, "application/octet-stream"
        return (
            metadata.output_slot or metadata.kind.value.replace("_", " "),
            None,
            self._mime(metadata, "utf8", None),
        )

    def delivery_bundle_source(
        self,
        session_id: str,
        artifact_id: str,
        *,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        node_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Registered snapshot bytes of one delivery entry and its resources.

        The scope is rebuilt exactly like a content read and every byte comes
        from its own Artifact, so a historical HTML preview can never borrow a
        newer workspace file. A missing or unauthorized resource is reported as
        a limitation instead of being substituted.
        """

        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
            )
        scope = self._resolve(
            session_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
        )
        allowed = self._authorized_artifacts(scope)
        found = self._delivery_entry_descriptor(scope, artifact_id)
        if found is None or artifact_id not in allowed:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND,
                "Artifact is not a registered delivery in this Task scope",
            )
        descriptor, _node = found
        files: list[tuple[str, bytes]] = [(descriptor.path, self._read_delivery_bytes(artifact_id))]
        missing = list(descriptor.limitations)
        for resource in descriptor.resources:
            metadata = allowed.get(resource.artifact_id)
            if metadata is None or metadata.kind is not ArtifactKind.DELIVERABLE:
                missing.append(f"{resource.path}（资源不在当前授权范围）")
                continue
            files.append((resource.path, self._read_delivery_bytes(resource.artifact_id)))
        return {
            "entry_path": descriptor.path,
            "entry_name": descriptor.name,
            "revision": descriptor.sha256,
            "mime": descriptor.mime,
            "files": tuple(files),
            "missing": tuple(missing),
        }

    def _delivery_entry_descriptor(self, scope: _Scope, artifact_id: str):
        """The one delivery entry descriptor this scope can prove names it."""

        if self.artifacts is None:
            return None
        for node in scope.nodes:
            marker = read_submission_marker(self.artifacts, node_run_id=node.node_run_id)
            if marker is None:
                continue
            for descriptor in marker.deliverables:
                if descriptor.artifact_id == artifact_id:
                    return descriptor, node
        return None

    def _read_delivery_bytes(self, artifact_id: str) -> bytes:
        metadata = self.journal.get_artifact(self.workspace_id, artifact_id)
        if metadata is None or metadata.byte_size > _DOWNLOAD_MAX_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "交付快照超过 20 MiB 读取上限")
        try:
            return self.artifacts.read(artifact_id, max_bytes=_DOWNLOAD_MAX_BYTES).content
        except ArtifactError:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "交付快照不可用") from None

    def _content_metadata(
        self, scope: _Scope, metadata: ArtifactMetadata, *, is_text: bool
    ) -> dict[str, Any]:
        """Name, path, MIME and preview family of the bytes a content read returns."""

        name, path, mime = self._artifact_names(scope, metadata)
        preview = _preview_family(name, mime)
        if preview == "binary" and is_text:
            # The bytes really decoded as UTF-8; a markerless Artifact type
            # must not turn them into a binary verdict.
            preview = "text"
        return {"name": name, "path": path, "mime": mime, "preview": preview}

    def _delivery_names(self, scope: _Scope, artifact_id: str) -> tuple[str, str, str] | None:
        """The verified delivery name/path/MIME of one snapshot in this scope."""

        if self.artifacts is None:
            return None
        for node in scope.nodes:
            marker = read_submission_marker(self.artifacts, node_run_id=node.node_run_id)
            if marker is None:
                continue
            for descriptor in marker.deliverables:
                if descriptor.artifact_id == artifact_id:
                    return descriptor.name, descriptor.path, descriptor.mime
                for resource in descriptor.resources:
                    if resource.artifact_id == artifact_id:
                        name = resource.path.rsplit("/", 1)[-1] or resource.path
                        return name, resource.path, media_type_for(resource.path)
        return None

    def _artifact_belongs_to_node(self, item: ArtifactMetadata, node) -> bool:
        return (
            item.workspace_id == self.workspace_id
            and self._leaf_task(node) is not None
            and item.producer_node_run_id == node.node_run_id
            and item.session_id == node.conversation_session_id
            and item.task_run_id == node.leaf_task_run_id
        )

    def _artifact_entry(self, item: ArtifactMetadata, budget: int, scope: _Scope):
        read = _ArtifactRead(None, self._artifact_availability(item))
        payload = None
        used = 0
        if (
            self.artifacts is not None
            and item.state is ArtifactState.AVAILABLE
            and item.kind in _REPORT_KINDS
            and budget > 0
        ):
            read_limit = min(_CONTENT_READ_MAX_BYTES, budget)
            try:
                content = self.artifacts.read(item.artifact_id, max_bytes=read_limit).content
                read = _ArtifactRead(content, "available")
                used = len(content)
                payload = self._parse_payload(item, content)
            except ArtifactError as exc:
                read = _ArtifactRead(
                    None, "missing" if exc.code.value == "artifact_missing" else "corrupt"
                )
        path, operation, status, diff, complete, omission, encoding = self._artifact_facts(
            item, read.content, payload
        )
        if read.availability != "available":
            availability = read.availability
        else:
            availability = "available"
        source = self._artifact_source(item)
        name = path or item.output_slot or item.kind.value.replace("_", " ")
        mime = self._mime(item, encoding, diff)
        if item.kind is ArtifactKind.DELIVERABLE:
            # A registered delivery reports its real name and type from the
            # verified marker of this scope; nothing is guessed from bytes.
            delivery_name, delivery_path, mime = self._artifact_names(scope, item)
            name = delivery_name
            path = delivery_path or path
        message = omission
        if availability == "staging":
            message = "产物仍在发布中，暂不可预览。"
        elif availability == "missing":
            message = "产物元数据仍在，但正文文件已缺失。"
        elif availability == "corrupt":
            message = "产物完整性校验失败，正文不可用。"
        elif (
            read.content is None
            and item.state is ArtifactState.AVAILABLE
            and item.kind in _REPORT_KINDS
        ):
            message = "预览超出本次读取预算；可按项重新打开。"
        provenance = tuple(
            ArtifactProvenanceWire(
                kind=ref.kind.value,
                role=ref.role,
                reference_id=ref.reference_id,
            )
            for ref in item.provenance_refs
        )
        entry = TaskArtifactWire(
            artifact_id=item.artifact_id,
            kind=item.kind,
            name=name,
            path=path,
            mime=mime,
            source=source,
            availability=availability,
            byte_size=item.byte_size,
            excerpt=item.excerpt,
            diff=diff,
            diff_truncated=(
                item.byte_size > _CONTENT_READ_MAX_BYTES
                or diff is not None
                and len(diff.encode()) >= _CONTENT_READ_MAX_BYTES
            ),
            content_complete=complete,
            content_encoding=encoding,
            omission_reason=omission,
            retention=item.retention.value,
            row_version=item.row_version,
            session_id=item.session_id,
            task_run_id=item.task_run_id,
            workflow_run_id=next(
                (
                    node.workflow_run_id
                    for node in scope.nodes
                    if node.node_run_id == item.producer_node_run_id
                ),
                None,
            ),
            node_run_id=item.producer_node_run_id,
            output_slot=item.output_slot,
            provenance=provenance,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        if message and entry.omission_reason is None:
            entry = entry.model_copy(update={"omission_reason": message})
        return entry, payload, used

    @staticmethod
    def _artifact_availability(item: ArtifactMetadata) -> str:
        return {
            ArtifactState.AVAILABLE: "available",
            ArtifactState.STAGING: "staging",
            ArtifactState.MISSING: "missing",
            ArtifactState.CORRUPT: "corrupt",
        }[item.state]

    @staticmethod
    def _parse_payload(item: ArtifactMetadata, content: bytes):
        contract = item.contract.kind if item.contract is not None else None
        if contract == "ChangeCapture":
            try:
                return ChangeCapture.model_validate_json(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if contract == "ImplementationPatch":
            try:
                return parse_workflow_payload(contract, content)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        return None

    @staticmethod
    def _artifact_facts(item: ArtifactMetadata, content: bytes | None, payload):
        path = operation = status = diff = omission = None
        complete = None
        encoding = "unknown"
        if isinstance(payload, ChangeCapture):
            path, operation, status = payload.path, payload.operation, payload.status
            diff = payload.unified_diff
            complete = payload.content_complete
            omission = payload.omission_reason
            encoding = "utf8"
        elif isinstance(payload, ImplementationPatch):
            operation, status = "patch", "recorded"
            complete = payload.content_complete
            omission = payload.omission_reason
            encoding = "utf8"
        elif item.contract is not None and item.contract.kind in {
            "ChangeCapture",
            "ImplementationPatch",
        }:
            complete = False
            omission = "变更证据正文无法完整读取；不会把结构化记录当作 Diff 显示。"
            encoding = "utf8"
        elif content is not None:
            text = content.decode("utf-8", errors="replace")
            encoding = "binary" if "\ufffd" in text else "utf8"
            if item.kind in _DIFF_KINDS and encoding == "utf8":
                diff = text
                complete = True
        return path, operation, status, diff, complete, omission, encoding

    @staticmethod
    def _mime(item: ArtifactMetadata, encoding: str, diff: str | None) -> str:
        if encoding == "binary":
            return "application/octet-stream"
        if diff is not None or item.kind in _DIFF_KINDS:
            return "text/x-diff"
        if item.kind in _REPORT_KINDS:
            return "application/json"
        return "text/plain"

    def _artifact_source(self, item: ArtifactMetadata):
        if item.kind is ArtifactKind.COMMAND_OUTPUT:
            return "command_output"
        if item.producer_node_run_id is not None:
            return "workflow_output"
        if any(ref.role in {"change_capture", "validation_report"} for ref in item.provenance_refs):
            return "task_evidence"
        if any(ref.kind.value == "task_outcome" for ref in item.provenance_refs):
            return "task_evidence"
        return "registered_result"

    def _file_changes(self, scope, metadata, artifact_entries, outcomes):
        entries_by_id = {item.artifact_id: item for item in artifact_entries}
        changes: dict[str, TaskFileChangeWire] = {}

        def put(value: TaskFileChangeWire) -> None:
            current = changes.get(value.path)
            if current is None or self._change_rank(value) > self._change_rank(current):
                changes[value.path] = value

        capture_by_tool: dict[tuple[str, str], str] = {}
        for item in artifact_entries:
            if item.path is None:
                continue
            for ref in item.provenance:
                if ref.kind == "tool_execution":
                    capture_by_tool[(ref.reference_id, item.path)] = item.artifact_id
            if item.kind in _DIFF_KINDS or item.source == "task_evidence":
                put(
                    TaskFileChangeWire(
                        path=item.path,
                        operation="change",
                        status="recorded",
                        artifact_id=item.artifact_id,
                        source=item.source,
                        availability=item.availability,
                        mime=item.mime,
                        diff=item.diff,
                        diff_truncated=item.diff_truncated,
                        content_complete=item.content_complete,
                        content_encoding=item.content_encoding,
                        omission_reason=item.omission_reason,
                        message=item.omission_reason,
                        updated_at=item.updated_at,
                    )
                )

        for task_id in scope.task_ids:
            for execution in self.journal.list_task_executions(self.workspace_id, task_id):
                if execution.facts is None:
                    continue
                for evidence in execution.facts.files:
                    artifact_id = capture_by_tool.get(
                        (execution.tool_execution_id, evidence.relative_path)
                    )
                    artifact = entries_by_id.get(artifact_id) if artifact_id else None
                    put(
                        TaskFileChangeWire(
                            path=evidence.relative_path,
                            operation=evidence.operation,
                            status=evidence.status or "recorded",
                            artifact_id=artifact_id,
                            source="task_evidence",
                            availability=artifact.availability if artifact else "missing",
                            mime=artifact.mime if artifact else "text/x-diff",
                            diff=artifact.diff if artifact else None,
                            diff_truncated=artifact.diff_truncated if artifact else False,
                            content_complete=artifact.content_complete if artifact else False,
                            content_encoding=artifact.content_encoding if artifact else "unknown",
                            omission_reason=(
                                artifact.omission_reason
                                if artifact
                                else "历史记录仅保存路径，未保存 Diff"
                            ),
                            message=(
                                artifact.omission_reason
                                if artifact
                                else "历史记录仅保存路径，未保存 Diff"
                            ),
                            updated_at=artifact.updated_at if artifact else execution.closed_at,
                        )
                    )

        for outcome in outcomes:
            for path in outcome.get("changed_paths", ()):
                put(
                    TaskFileChangeWire(
                        path=path,
                        operation="change",
                        status="recorded",
                        source="task_evidence",
                        availability="missing",
                        mime="text/x-diff",
                        content_complete=False,
                        omission_reason="历史记录仅保存路径，未保存 Diff",
                        message="历史记录仅保存路径，未保存 Diff",
                    )
                )
        return sorted(changes.values(), key=lambda value: value.path)

    @staticmethod
    def _change_rank(value: TaskFileChangeWire) -> tuple[int, int, str]:
        availability = {"available": 3, "staging": 2, "missing": 1}.get(value.availability, 0)
        return (
            1 if value.diff else 0,
            availability,
            value.updated_at.isoformat() if value.updated_at else "",
        )

    def _command_outputs(self, scope, metadata):
        result: list[CommandOutputWire] = []
        by_id = metadata
        for task_id in scope.task_ids:
            for execution in self.journal.list_task_executions(self.workspace_id, task_id):
                if execution.tool_name not in _COMMAND_TOOLS:
                    continue
                facts = (
                    execution.facts.commands[0]
                    if execution.facts and execution.facts.commands
                    else None
                )
                artifact = self._command_artifact(execution, by_id)
                output_availability = (
                    self._artifact_availability(artifact) if artifact else "not_persisted"
                )
                output_source = "artifact" if artifact is not None else "none"
                message = None
                if artifact is None:
                    message = "历史执行只保留元数据，输出正文未保存。"
                elif output_availability == "missing":
                    message = "命令输出 Artifact 已登记，但正文文件已缺失。"
                elif output_availability == "corrupt":
                    message = "命令输出 Artifact 完整性校验失败。"
                elif facts is None:
                    message = "旧记录未保存命令 cwd、退出码与耗时的完整元数据。"
                result.append(
                    CommandOutputWire(
                        tool_execution_id=execution.tool_execution_id,
                        tool_name=execution.tool_name,
                        command_class=facts.command_class if facts else None,
                        cwd=facts.cwd if facts else None,
                        ordinal=execution.ordinal,
                        state=execution.state.value,
                        disposition=execution.disposition.value,
                        started_at=execution.created_at,
                        ended_at=execution.closed_at,
                        duration_ms=facts.duration_ms if facts else None,
                        exit_code=facts.exit_code if facts else None,
                        signal=facts.signal if facts else None,
                        output_artifact_id=artifact.artifact_id if artifact else None,
                        output_availability=output_availability,
                        output_excerpt=artifact.excerpt if artifact else "",
                        output_truncated=(facts.output_truncated if facts else False)
                        or bool(artifact and artifact.byte_size > _CONTENT_READ_MAX_BYTES),
                        output_source=output_source,
                        message=message,
                    )
                )
        return sorted(
            result, key=lambda item: (item.started_at, item.ordinal, item.tool_execution_id)
        )

    @staticmethod
    def _command_artifact(execution, metadata):
        candidates = []
        refs = {ref.artifact_id for ref in execution.artifact_refs if ref.role == "tool_output"}
        for item in metadata.values():
            if item.kind is not ArtifactKind.COMMAND_OUTPUT:
                continue
            if item.artifact_id in refs:
                candidates.append(item)
                continue
            if any(
                ref.kind.value == "tool_execution"
                and ref.reference_id == execution.tool_execution_id
                for ref in item.provenance_refs
            ):
                candidates.append(item)
        return max(candidates, key=lambda item: (item.created_at, item.artifact_id), default=None)

    @staticmethod
    def _overall_availability(artifacts, files, commands):
        if not artifacts and not files and not commands:
            return "empty"
        values = [item.availability for item in artifacts] + [item.availability for item in files]
        values += [item.output_availability for item in commands]
        return "partial" if any(value not in {"available"} for value in values) else "available"

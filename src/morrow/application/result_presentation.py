"""Bounded, source-verified projection of one TaskOutcome for chat display.

The chat transcript already carries a durable `result` timeline item per
TaskOutcome, but that item only referenced its source fact: nothing of the
actual answer, its declared outputs or its files reached the conversation.
This module turns one durable TaskOutcome into that display projection.

Hard rules kept here:

* the projection is derived from the Outcome's own durable record and the
  Artifacts reachable through the *already authorized* TaskArtifacts scope —
  never from the caller's current task and never by guessing paths;
* only the declared final output order (the Outcome's `artifact_refs`, which
  the Workflow finalizer builds from `revision.required_outputs`) provides
  generated deliverables; plan target paths, read files and command logs are
  not deliverables;
* every value is bounded, and an unknown or damaged contract degrades to a
  bounded note plus the Outcome summary instead of breaking the page.

No business state, no second transcript and no model call lives here.
"""

from __future__ import annotations

from typing import Any

from morrow.application.workflows.deliveries import resolve_run_deliveries
from morrow.core.application import ApplicationError
from morrow.core.artifacts import ArtifactError, ArtifactMetadata, ArtifactState
from morrow.core.domain import TaskOutcome, TaskOutcomeEvidenceKind, sha256_digest
from morrow.core.store import StorageError
from morrow.core.workflows.contracts import (
    EvidenceBundle,
    ImplementationPatch,
    PlanArtifact,
    ReviewReport,
    SynthesisReport,
    TestReport,
    TextResult,
    parse_workflow_payload,
)

#: Inline result body budget; mirrors the activity-content bound (16 KiB).
RESULT_BODY_MAX_BYTES = 16384

#: Bounded sections/files/notes per projection.
RESULT_MAX_SECTIONS = 8
RESULT_MAX_SECTION_ITEMS = 32
RESULT_MAX_FILES = 64
RESULT_MAX_NOTES = 8
RESULT_MAX_SECTION_CHARS = 512
RESULT_MAX_NOTE_CHARS = 256
RESULT_MAX_LABEL_CHARS = 64

_NOT_READABLE = "结果正文无法读取；仅显示已登记摘要。"


def _bounded(value: str, limit: int) -> str:
    """One summary/label line: surrounding whitespace collapses, layout never matters."""

    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    """Bounded result body text; newlines and indentation are preserved.

    The byte budget is applied to the encoded text and a cut multi-byte tail is
    dropped rather than replaced, so a truncated body still decodes cleanly.
    """

    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _lines(values, *, limit: int = RESULT_MAX_SECTION_ITEMS) -> list[str]:
    return [_bounded(item, RESULT_MAX_SECTION_CHARS) for item in values if str(item).strip()][
        :limit
    ]


def _section(label: str, values, *, kind: str = "lines") -> dict[str, Any] | None:
    items = _lines(values)
    if not items:
        return None
    return {"label": _bounded(label, RESULT_MAX_LABEL_CHARS), "kind": kind, "items": items}


class TaskResultProjector:
    """Project one durable TaskOutcome into its chat display wire.

    ``task_artifacts`` is the existing scope-validating TaskArtifacts service;
    it is the only source of authorized Artifact metadata. ``artifacts`` is the
    Artifact Store used to read bounded payload bytes.
    """

    def __init__(
        self,
        journal,
        task_artifacts=None,
        *,
        artifacts=None,
        workspace_id: str,
        workflow_queries=None,
    ) -> None:
        self.journal = journal
        self.task_artifacts = task_artifacts
        self.artifacts = artifacts
        self.workspace_id = workspace_id
        # Explicit wiring when the server composition has the Workflow query
        # projection; the TaskArtifacts service already holds the same one.
        self.workflow_queries = workflow_queries or getattr(
            task_artifacts, "workflow_queries", None
        )

    # Public ---------------------------------------------------------------

    def project(self, outcome: TaskOutcome) -> dict[str, Any]:
        """Best-effort bounded projection; never raises for a broken source."""

        notes: list[str] = []
        allowed = self._authorized(outcome, notes)
        sections: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        body: dict[str, Any] | None = None
        body_ref: dict[str, Any] | None = None

        # Files are only the explicitly registered, exported deliveries.
        for entry in self._delivery_entries(outcome, allowed, notes):
            if len(files) >= RESULT_MAX_FILES:
                notes.append("结果文件过多，仅显示前若干项。")
                break
            files.append(entry)

        # Declared output contracts are the answer and its readable conclusions,
        # never delivery files: the Diff/ChangeCapture records stay execution
        # evidence in the TaskArtifacts detail.
        for reference in outcome.artifact_refs:
            metadata = allowed.get(reference.artifact_id)
            if metadata is None:
                notes.append(f"结果产物 {reference.artifact_id} 不在此结果的可读范围内，已跳过。")
                continue
            payload = self._payload(metadata, notes)
            if payload is None:
                continue
            self._merge_payload(payload, sections)
            if isinstance(payload, TextResult) and body is None:
                body, body_ref = self._text_body(metadata, payload, outcome, notes)

        for path in outcome.changed_paths:
            if len(files) >= RESULT_MAX_FILES:
                notes.append("变更路径过多，仅显示前若干项。")
                break
            if any(item["path"] == path for item in files):
                continue
            files.append(
                {
                    "label": _bounded(path, RESULT_MAX_SECTION_CHARS),
                    "kind": "changed_path",
                    "role": "changed",
                    "source": "task_evidence",
                    "artifact_id": None,
                    "path": path,
                    "mime": None,
                    "byte_size": None,
                    "availability": "missing",
                    "inherited": False,
                    "node_id": None,
                    "output_slot": None,
                    "note": _bounded(
                        "结果只记录了变更路径，未保存该文件的完整正文。", RESULT_MAX_NOTE_CHARS
                    ),
                }
            )

        run = self._workflow_run(outcome)
        return {
            "outcome_id": outcome.outcome_id,
            "task_run_id": outcome.task_run_id,
            "workflow_run_id": run.workflow_run_id if run is not None else None,
            "task_status": outcome.task_status.value,
            "result_status": getattr(run, "result_status", None) if run is not None else None,
            "trigger": outcome.trigger.value,
            "version": outcome.version,
            "summary": _bounded(outcome.summary, 2000),
            "sections": sections[:RESULT_MAX_SECTIONS],
            "files": files,
            "body": body,
            "body_ref": body_ref,
            "notes": notes[:RESULT_MAX_NOTES],
            "truncated": bool(notes),
        }

    # Authorization --------------------------------------------------------

    def _authorized(self, outcome: TaskOutcome, notes: list[str]) -> dict[str, ArtifactMetadata]:
        """The already authorized Artifact set for this Outcome's own scope.

        The Outcome carries its own session/task identity, so the TaskArtifacts
        scope is rebuilt for *that* task: a viewer can never widen a historical
        result by holding a newer task selection.
        """

        if self.task_artifacts is None:
            return {}
        try:
            return self.task_artifacts.authorized_artifacts_for_task(
                outcome.session_id, outcome.task_run_id
            )
        except ApplicationError:
            notes.append("结果来源已不属于当前会话范围，仅显示已登记摘要。")
            return {}
        except StorageError:
            notes.append("结果产物记录无法读取，仅显示已登记摘要。")
            return {}

    def _workflow_run(self, outcome: TaskOutcome):
        for reference in outcome.evidence_refs:
            if reference.kind is not TaskOutcomeEvidenceKind.WORKFLOW_RUN:
                continue
            run = self.journal.workflows.get_run(self.workspace_id, reference.reference_id)
            if run is not None:
                return run
        return None

    # Payload --------------------------------------------------------------

    def _payload(self, metadata: ArtifactMetadata, notes: list[str]):
        if metadata.state is not ArtifactState.AVAILABLE:
            notes.append(_NOT_READABLE)
            return None
        if self.artifacts is None:
            notes.append(_NOT_READABLE)
            return None
        contract = metadata.contract.kind if metadata.contract is not None else None
        if contract is None:
            return None
        try:
            read = self.artifacts.read(metadata.artifact_id, max_bytes=RESULT_BODY_MAX_BYTES * 4)
        except ArtifactError:
            notes.append("结果正文无法读取（完整性或存储不可用）；已保留摘要与文件入口。")
            return None
        try:
            return parse_workflow_payload(contract, read.content)
        except (ValueError, TypeError):
            notes.append(f"结果合同 {contract} 无法解析；已保留摘要与文件入口。")
            return None

    def _merge_payload(self, payload, sections: list[dict[str, Any]]) -> None:
        for section in _payload_sections(payload):
            if len(sections) >= RESULT_MAX_SECTIONS:
                return
            sections.append(section)

    def _text_body(
        self,
        metadata: ArtifactMetadata,
        payload: TextResult,
        outcome: TaskOutcome,
        notes: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The answer itself, read from its own durable record when possible."""

        body_ref = {
            "record_id": payload.final_assistant_record_id,
            "sha256": payload.final_assistant_sha256,
            "content_complete": payload.content_complete,
        }
        content, verified = self._record_content(metadata, payload, outcome, notes)
        if content is None:
            text, _cut = _bounded_text(payload.excerpt, RESULT_BODY_MAX_BYTES)
            notes.append("结果正文无法按原记录读取；仅显示已登记摘要。")
            return (
                {
                    "kind": "text",
                    "text": text,
                    "truncated": True,
                    "content_complete": False,
                },
                body_ref,
            )
        text, cut = _bounded_text(content, RESULT_BODY_MAX_BYTES)
        complete = payload.content_complete and verified and not cut
        return (
            {
                "kind": "text",
                "text": text,
                "truncated": not complete,
                "content_complete": complete,
            },
            body_ref,
        )

    def _record_content(
        self,
        metadata: ArtifactMetadata,
        payload: TextResult,
        outcome: TaskOutcome,
        notes: list[str],
    ) -> tuple[str | None, bool]:
        """Read the referenced final Assistant record inside the Outcome scope.

        The record's own session must be the Outcome's session or a leaf session
        of its Workflow run; this never opens arbitrary leaf chat history. The
        second value is the digest verdict: a body that no longer matches the
        registered hash is shown but never called complete.
        """

        session_id = metadata.session_id
        if session_id is None or not self._session_in_scope(session_id, outcome):
            return None, False
        try:
            records = self.journal.load_effective_records(self.workspace_id, session_id)
        except (StorageError, ApplicationError):
            return None, False
        record = next(
            (item for item in records if item.record_id == payload.final_assistant_record_id),
            None,
        )
        if record is None or record.kind != "message":
            return None, False
        content = record.payload.get("content") if isinstance(record.payload, dict) else None
        if not isinstance(content, str) or not content:
            return None, False
        if sha256_digest(content) != payload.final_assistant_sha256:
            notes.append("结果正文与登记摘要不一致；以记录正文为准。")
            return content, False
        return content, True

    def _session_in_scope(self, session_id: str, outcome: TaskOutcome) -> bool:
        if session_id == outcome.session_id:
            return True
        run = self._workflow_run(outcome)
        if run is None:
            return False
        try:
            nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        except ApplicationError:
            return False
        return any(node.conversation_session_id == session_id for node in nodes)

    # Deliveries -----------------------------------------------------------

    def _delivery_entries(
        self, outcome: TaskOutcome, allowed: dict[str, ArtifactMetadata], notes: list[str]
    ) -> list[dict[str, Any]]:
        """Only the declared, exported deliveries of this Outcome's own run."""

        if self.artifacts is None or self.workflow_queries is None:
            return []
        run = self._workflow_run(outcome)
        if run is None:
            return []
        view = self.workflow_queries.get_run_view(run.workflow_run_id)
        if view is None:
            return []
        entries: list[dict[str, Any]] = []
        for delivery in resolve_run_deliveries(view, artifacts=self.artifacts):
            metadata = allowed.get(delivery.descriptor.artifact_id)
            if metadata is None:
                notes.append("部分交付件不在结果的可读范围内，已跳过。")
                continue
            entries.append(self._delivery_entry(delivery, metadata))
        return entries

    @staticmethod
    def _delivery_entry(delivery, metadata: ArtifactMetadata) -> dict[str, Any]:
        descriptor = delivery.descriptor
        note = None
        if descriptor.limitations:
            note = "部分静态依赖未纳入交付：" + descriptor.limitations[0]
        return {
            "label": _bounded(
                descriptor.label or descriptor.name or descriptor.path,
                RESULT_MAX_SECTION_CHARS,
            ),
            "kind": "deliverable",
            "role": "delivery",
            "source": "workflow_delivery",
            "artifact_id": descriptor.artifact_id,
            "path": descriptor.path,
            "name": descriptor.name,
            "mime": descriptor.mime,
            "byte_size": descriptor.byte_size,
            "availability": (
                "available" if metadata.state is ArtifactState.AVAILABLE else metadata.state.value
            ),
            "inherited": delivery.inherited,
            "node_id": delivery.producer_node_run_id,
            "output_slot": descriptor.slot,
            "resource_count": len(descriptor.resources),
            "note": _bounded(note, RESULT_MAX_NOTE_CHARS) if note else None,
        }


def _payload_sections(payload) -> list[dict[str, Any]]:
    """Readable sections for one parsed output contract.

    `TextResult` has no sections: its answer is the body. Unknown contracts keep
    their summary only — a corrupted contract never blocks the rest.
    """

    candidates: list[dict[str, Any] | None] = []
    if isinstance(payload, TextResult):
        return []
    if isinstance(payload, SynthesisReport):
        candidates = [
            _section("综合结论", [payload.summary], kind="text"),
            _section("发现", payload.findings),
            _section("来源", payload.source_refs),
            _section("不确定项", payload.uncertainties),
        ]
    elif isinstance(payload, ReviewReport):
        candidates = [
            _section("评审结论", [_review_verdict(payload)], kind="text"),
            _section("发现", payload.findings),
            _section("必须修改", payload.required_changes),
            _section("可选建议", payload.optional_notes),
            _section("证据", payload.evidence_refs),
        ]
    elif isinstance(payload, TestReport):
        candidates = [
            _section("验证结果", (_test_item_label(item) for item in payload.items)),
        ]
        if payload.omission_reason:
            candidates.append(_section("说明", [payload.omission_reason], kind="text"))
    elif isinstance(payload, ImplementationPatch):
        candidates = [
            _section("实现说明", [payload.rationale], kind="text"),
            _section("变更文件", payload.changed_paths),
        ]
        if payload.omission_reason:
            candidates.append(_section("说明", [payload.omission_reason], kind="text"))
    elif isinstance(payload, EvidenceBundle):
        candidates = [
            _section("发现", payload.findings),
            _section("来源", payload.source_refs),
            _section("相关文件", payload.relevant_paths),
            _section("不确定项", payload.uncertainties),
        ]
    elif isinstance(payload, PlanArtifact):
        candidates = [
            _section("步骤", payload.steps),
            _section("计划涉及路径", payload.target_paths),
            _section("验证", payload.validation),
            _section("风险", payload.risks),
        ]
    return [item for item in candidates if item is not None]


def _review_verdict(payload: ReviewReport) -> str:
    labels = {"approve": "通过", "request_changes": "要求修改", "block": "阻塞"}
    return f"{labels.get(payload.verdict, payload.verdict)}（{payload.severity}）"


def _test_item_label(item) -> str:
    parts = [item.validator_kind, item.scope, item.status]
    if item.exit_code is not None:
        parts.append(f"exit={item.exit_code}")
    parts.append(item.evidence_summary)
    return _bounded(" · ".join(part for part in parts if part), RESULT_MAX_SECTION_CHARS)

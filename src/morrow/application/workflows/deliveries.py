"""Explicit Workflow delivery files: confined read, snapshot, marker.

A registered delivery is read once through the same confined workspace
resolver the node's file tools use, published as an ordinary-file Artifact and
described by a server-derived descriptor. The model only names a
workspace-relative path and an optional label; name, MIME, hash, size and
Artifact identity are produced here. Snapshot identities are keyed by the
verified bytes, so a retry reuses the original snapshot and can never mix one
attempt's descriptor with another attempt's content. The submission marker is
written last, so an interrupted submission never exposes a half delivery.
"""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.artifacts import ArtifactService
from morrow.application.html_preview import HtmlPreviewBuilder
from morrow.application.workspace_files import media_type_for
from morrow.core.application import ApplicationError
from morrow.core.artifacts import (
    ArtifactError,
    ArtifactErrorCode,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactState,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.workflows.contracts import (
    DELIVERY_FILE_MAX_BYTES,
    DELIVERY_MARKER_MAX_BYTES,
    DELIVERY_TOTAL_MAX_BYTES,
    SUBMIT_SCHEMA_V1,
    DeliverableRequest,
    DeliveryDescriptor,
    DeliveryResource,
    NodeSubmissionMarker,
    node_submission_artifact_id,
)
from morrow.services.files import LocalFileError, WorkspaceFileService, WorkspacePathResolver

_HTML_SUFFIXES = frozenset({"html", "htm"})
_MARKER_EXCERPT = "structured node result submission"


class DeliveryError(RuntimeError):
    """Bounded delivery failure; its message is safe for a Tool receipt."""


def delivery_artifact_id(node_run_id: str, slot: str, path: str, sha256: str) -> str:
    """Content-and-slot addressed identity of one delivery snapshot.

    The verified bytes are part of the identity: an identical retry reuses the
    same Artifact, while changed bytes publish a fresh snapshot instead of
    overwriting the previous one.
    """

    identity = [node_run_id, "delivery", slot, path, sha256]
    return "art_" + sha256_digest(canonical_json_bytes(identity))[:32]


def file_name_for(path: str) -> str:
    return path.rsplit("/", 1)[-1] or path


def is_html_path(path: str) -> bool:
    name = file_name_for(path)
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return suffix in _HTML_SUFFIXES


@dataclass(frozen=True)
class DeliveryRead:
    """One verified in-memory read, with its captured static dependencies."""

    slot: str
    path: str
    name: str
    #: Optional display label the model registered for this entry.
    label: str | None = None
    mime: str = "application/octet-stream"
    content: bytes = b""
    resources: tuple[DeliveryRead, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def all_files(self) -> tuple[DeliveryRead, ...]:
        return (self, *self.resources)


class WorkflowDeliveryReader:
    """Bounded, workspace-confined read of every explicitly registered file."""

    def __init__(self, root, *, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.files = WorkspaceFileService(WorkspacePathResolver(root))

    def read_all(
        self, requests_by_slot: dict[str, tuple[DeliverableRequest, ...]]
    ) -> tuple[DeliveryRead, ...]:
        reads: list[DeliveryRead] = []
        total = 0
        for slot in sorted(requests_by_slot):
            for request in requests_by_slot[slot]:
                read = self._read(slot, request)
                for item in read.all_files:
                    if len(item.content) > DELIVERY_FILE_MAX_BYTES:
                        raise DeliveryError(f"{item.path} 超过单文件 20 MiB 交付上限")
                    total += len(item.content)
                if total > DELIVERY_TOTAL_MAX_BYTES:
                    raise DeliveryError("本次登记的文件总字节超过 20 MiB 上限")
                reads.append(read)
        return tuple(reads)

    def _read(self, slot: str, request: DeliverableRequest) -> DeliveryRead:
        if is_html_path(request.path):
            return self._read_html(slot, request)
        try:
            resolved = self.files.resolver.resolve_file(request.path)
        except LocalFileError as exc:
            raise DeliveryError(f"{request.path} 不可交付：{exc.message}") from None
        if resolved.kind != "file":
            raise DeliveryError(f"{request.path} 不是普通文件，无法交付")
        raw = self._read_bytes(resolved.target, resolved.relative_path)
        return DeliveryRead(
            slot=slot,
            path=resolved.relative_path,
            name=file_name_for(resolved.relative_path),
            label=request.label,
            mime=media_type_for(resolved.relative_path),
            content=raw,
        )

    def _read_html(self, slot: str, request: DeliverableRequest) -> DeliveryRead:
        """The entry plus its bounded static local collection, captured once."""

        path = request.path
        builder = HtmlPreviewBuilder(self.files, workspace_id=self.workspace_id)
        try:
            bundle = builder.build(path)
        except ApplicationError as exc:
            raise DeliveryError(f"{path} 的静态依赖集合无法构建：{exc.message}") from None
        entry = bundle.file(bundle.entry_path)
        if entry is None:
            raise DeliveryError(f"{path} 的交付入口不可读取")
        resources = tuple(
            DeliveryRead(
                slot=slot,
                path=item.path,
                name=file_name_for(item.path),
                mime=media_type_for(item.path),
                content=item.content,
            )
            for item in bundle.files
            if item.path != bundle.entry_path
        )
        return DeliveryRead(
            slot=slot,
            path=entry.path,
            name=file_name_for(entry.path),
            label=request.label,
            mime=media_type_for(entry.path),
            content=entry.content,
            resources=resources,
            limitations=bundle.missing[:64],
        )

    def _read_bytes(self, target, path: str) -> bytes:
        try:
            return self.files.filesystem.read_bytes(target, max_bytes=DELIVERY_FILE_MAX_BYTES)
        except LocalFileError as exc:
            raise DeliveryError(f"{path} 不可交付：{exc.message}") from None


def publish_delivery_snapshots(
    artifacts: ArtifactService,
    reads: tuple[DeliveryRead, ...],
    *,
    node_run_id: str,
    session_id: str,
    task_run_id: str,
    tool_execution_id: str | None = None,
) -> tuple[DeliveryDescriptor, ...]:
    """Publish every verified read as an ordinary-file Artifact snapshot."""

    descriptors: list[DeliveryDescriptor] = []
    for read in reads:
        resources: list[DeliveryResource] = []
        for item in read.resources:
            metadata = _ensure_snapshot(
                artifacts,
                item,
                node_run_id=node_run_id,
                session_id=session_id,
                task_run_id=task_run_id,
                tool_execution_id=tool_execution_id,
            )
            resources.append(
                DeliveryResource(
                    path=item.path,
                    artifact_id=metadata.artifact_id,
                    sha256=metadata.sha256,
                    byte_size=metadata.byte_size,
                )
            )
        entry = _ensure_snapshot(
            artifacts,
            read,
            node_run_id=node_run_id,
            session_id=session_id,
            task_run_id=task_run_id,
            tool_execution_id=tool_execution_id,
        )
        descriptors.append(
            DeliveryDescriptor(
                slot=read.slot,
                path=read.path,
                name=read.name,
                label=read.label,
                mime=read.mime,
                artifact_id=entry.artifact_id,
                sha256=entry.sha256,
                byte_size=entry.byte_size,
                resources=tuple(resources),
                limitations=read.limitations,
            )
        )
    return tuple(descriptors)


@dataclass(frozen=True)
class RunDelivery:
    """One delivery a run exports, resolved along its verified source chain."""

    node_id: str
    output_slot: str
    descriptor: DeliveryDescriptor
    producer_node_run_id: str
    inherited: bool


def resolve_run_deliveries(view, *, artifacts: ArtifactService) -> tuple[RunDelivery, ...]:
    """Only the declared, exported slots of one run view, in declared order.

    The chain stays frozen revision -> effective output -> the real producer
    node run -> that node's verified submission marker. A missing or incomplete
    marker contributes nothing, and a registered file under a slot the revision
    does not export never enters this list.
    """

    required = [(ref.node_id, ref.output_slot) for ref in view.revision.required_outputs]
    order = {key: index for index, key in enumerate(required)}
    resolved: list[RunDelivery] = []
    for output in view.effective_outputs:
        key = (output.node_id, output.output_slot)
        if key not in order:
            continue
        artifact = output.artifact
        if artifact is None or artifact.producer_node_run_id is None:
            continue
        marker = read_submission_marker(artifacts, node_run_id=artifact.producer_node_run_id)
        if marker is None:
            continue
        for descriptor in marker.deliverables:
            if descriptor.slot != output.output_slot:
                continue
            resolved.append(
                RunDelivery(
                    node_id=output.node_id,
                    output_slot=output.output_slot,
                    descriptor=descriptor,
                    producer_node_run_id=artifact.producer_node_run_id,
                    inherited=output.inherited,
                )
            )
    resolved.sort(key=lambda item: (order[(item.node_id, item.output_slot)], item.descriptor.path))
    return tuple(resolved)


def _ensure_snapshot(
    artifacts: ArtifactService,
    read: DeliveryRead,
    *,
    node_run_id: str,
    session_id: str,
    task_run_id: str,
    tool_execution_id: str | None,
) -> ArtifactMetadata:
    """Idempotent publish of one immutable byte-exact snapshot."""

    artifact_id = delivery_artifact_id(
        node_run_id, read.slot, read.path, sha256_digest(read.content)
    )
    provenance = (
        (
            ArtifactProvenanceRef(
                kind=ArtifactProvenanceKind.TOOL_EXECUTION,
                reference_id=tool_execution_id,
                role="delivery_submission",
            ),
        )
        if tool_execution_id
        else ()
    )
    prior = artifacts.get(artifact_id)
    try:
        if prior is None:
            return artifacts.publish_bytes(
                read.content,
                kind=ArtifactKind.DELIVERABLE,
                session_id=session_id,
                task_run_id=task_run_id,
                provenance_refs=provenance,
                excerpt=f"交付文件 {read.name}"[:128],
                artifact_id=artifact_id,
            )
        if prior.state is ArtifactState.STAGING:
            recovered = artifacts.finalize_staging(artifact_id)
            if recovered.state is ArtifactState.STAGING:
                recovered = artifacts.restore_staging_bytes(read.content, artifact_id=artifact_id)
            if recovered.state is not ArtifactState.AVAILABLE:
                raise DeliveryError("交付快照未能完成发布，请重试")
            prior = recovered
        if prior.state is not ArtifactState.AVAILABLE:
            raise DeliveryError(f"{read.path} 的交付快照已不可用")
        if prior.sha256 != sha256_digest(read.content) or prior.byte_size != len(read.content):
            raise DeliveryError(f"{read.path} 的交付快照与已发布字节不一致")
        artifacts.read(artifact_id, max_bytes=prior.byte_size)
    except ArtifactError as exc:
        if exc.code is ArtifactErrorCode.CONFLICT:
            raise DeliveryError(f"{read.path} 的交付快照与已发布内容冲突") from None
        raise DeliveryError(exc.message) from None
    return prior


def read_submission_marker(artifacts: ArtifactService, *, node_run_id: str):
    """The one completed submission of a NodeRun, or None while incomplete."""

    metadata = artifacts.get(node_submission_artifact_id(node_run_id))
    if metadata is None or metadata.state is not ArtifactState.AVAILABLE:
        return None
    raw = artifacts.read(metadata.artifact_id, max_bytes=metadata.byte_size).content
    try:
        return NodeSubmissionMarker.model_validate_json(raw)
    except ValueError:
        return None


def marker_payload(marker: NodeSubmissionMarker, *, protocol_version: int) -> bytes:
    """Durable marker bytes; a v1 run keeps writing exactly its frozen shape."""

    if protocol_version == SUBMIT_SCHEMA_V1:
        body: object = {
            "digest": marker.digest,
            "slots": list(marker.slots),
            "replan": marker.replan,
        }
    else:
        body = marker.model_dump(mode="json")
    payload = canonical_json_bytes(body)
    if len(payload) > DELIVERY_MARKER_MAX_BYTES:
        raise DeliveryError("提交记录超过其大小上限")
    return payload


def write_submission_marker(
    artifacts: ArtifactService,
    marker: NodeSubmissionMarker,
    *,
    node_run_id: str,
    protocol_version: int,
    session_id: str,
    task_run_id: str,
) -> ArtifactMetadata:
    try:
        return artifacts.publish_bytes(
            marker_payload(marker, protocol_version=protocol_version),
            kind=ArtifactKind.TASK_SUMMARY,
            session_id=session_id,
            task_run_id=task_run_id,
            artifact_id=node_submission_artifact_id(node_run_id),
            excerpt=_MARKER_EXCERPT,
        )
    except ArtifactError as exc:
        if exc.code is ArtifactErrorCode.CONFLICT:
            raise DeliveryError("该节点已有冲突的提交记录") from None
        raise DeliveryError(exc.message) from None

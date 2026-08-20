"""Canonical, untrusted rendering of immutable Project Knowledge revisions."""

from __future__ import annotations

import json
from collections.abc import Sequence

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning_memory import ProjectKnowledgeHead, ProjectKnowledgeRevision


def render_project_knowledge(head: ProjectKnowledgeHead, revision: ProjectKnowledgeRevision) -> str:
    """Return the bounded canonical JSON block shared by selection and ContextBuilder."""

    payload = {
        "trust": "untrusted_project_knowledge",
        "record_kind": "project_knowledge",
        "record_id": head.knowledge_id,
        "record_revision_id": revision.knowledge_revision_id,
        "revision": revision.revision,
        "category": head.category.value,
        "semantic_key": head.semantic_key,
        "statement": revision.statement,
    }
    return canonical_json_bytes(payload).decode("utf-8")


def project_knowledge_content_digest(
    head: ProjectKnowledgeHead, revision: ProjectKnowledgeRevision
) -> str:
    return sha256_digest(render_project_knowledge(head, revision))


def render_project_knowledge_block(
    records: Sequence[tuple[ProjectKnowledgeHead, ProjectKnowledgeRevision]],
) -> str:
    """Return one canonical, bounded JSON block for a frozen selection."""

    payload = {
        "trust": "untrusted_project_knowledge",
        "records": [
            json.loads(render_project_knowledge(head, revision)) for head, revision in records
        ],
    }
    return canonical_json_bytes(payload).decode("utf-8")


def project_knowledge_block_digest(
    records: Sequence[tuple[ProjectKnowledgeHead, ProjectKnowledgeRevision]],
) -> str:
    return sha256_digest(render_project_knowledge_block(records))


__all__ = [
    "project_knowledge_block_digest",
    "project_knowledge_content_digest",
    "render_project_knowledge",
    "render_project_knowledge_block",
]

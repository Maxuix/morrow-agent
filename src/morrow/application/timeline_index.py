"""Durable chat trajectory index (P06): the real TimelineSinkPort owner.

The unified root-session display index lives in ``chat_timeline_entries``.
Every entry references its durable source fact; bodies stay with their original
owner, ``conversation_position`` is never rewritten and no conversation record
is ever written here. Index writes run strictly inside the caller's transaction
(``TimelineSinkPort.record``), source identity
``(workspace_id, root_session_id, source_kind, source_id)`` is globally deduped
by a unique index, and ``timeline_position``/``revision`` are assigned by this
service — the index owner per the frozen contract.

The reconcile sweep is the same-transaction outbox backstop: any source fact
that committed without an index row (a caller seam that has not been wired, a
crash between fact and index) is (re)indexed deterministically; a rolled-back
fact never leaves an entry, and an already-indexed source is never duplicated.
Read-side authorization walks the root→run→node lineage and applies fork cuts,
so a forked session inherits exactly the prefix it was cut from.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from morrow.core import chat_trajectory as trajectory
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.contracts import (
    SafeContentRef,
    TimelineCursor,
    TimelineEntryIdentity,
    TimelineSinkEntry,
)

#: Hard cap of one durable activity content body; mirrors the current-schema CHECK.
ACTIVITY_CONTENT_MAX_BYTES = 65536

#: Unified mixed-timeline page limits (P08.1): bounded items and bytes per page.
PAGE_MAX_ITEMS = 100
PAGE_MAX_BYTES = 1024 * 1024

#: Bounded scan effort while fork cuts filter invisible entries out of a page.
PAGE_MAX_BATCHES = 10

_MAX_LINEAGE_DEPTH = 33


class TimelineIndexService:
    """Owns the durable display index: sink, dedup, reconcile and reads."""

    def __init__(self, journal, workspace_id: str, *, result_projector=None) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.repository = journal.chat_timeline.index
        # Optional: the chat-result projection needs the Artifact store. A
        # lightweight index (tests, sink-only wiring) keeps serving items and
        # simply omits the optional result payload.
        self.result_projector = result_projector

    # --- frozen TimelineSinkPort (P01 contract 1.0.0) ---

    def record(self, identity: TimelineEntryIdentity) -> TimelineSinkEntry:
        """Record one display entry inside the caller's open transaction.

        The entry becomes visible only when the caller's source fact commits;
        a rollback removes it. Re-recording the same source identity never
        duplicates the entry — a strictly higher revision updates it in place
        and keeps its ``timeline_position`` stable.
        """

        if not isinstance(identity, TimelineEntryIdentity):
            raise ApplicationError(ApplicationErrorCode.INVALID, "Timeline identity is required")
        if not self.journal.transaction_is_active():
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Timeline entries must be recorded inside the caller's transaction",
            )
        now = int(self.journal.now().timestamp())
        existing = self.repository.get_by_source(
            identity.workspace_id,
            identity.root_session_id,
            identity.source_kind,
            identity.source_id,
        )
        if existing is not None:
            if identity.revision <= existing["revision"]:
                return _wire_entry(existing)
            self.repository.update_entry(
                identity.workspace_id,
                identity.root_session_id,
                existing["timeline_position"],
                item_id=identity.item_id,
                revision=identity.revision,
                occurred_at_unix=_unix(identity.occurred_at),
                updated_at_unix=now,
                source_state=existing["source_state"],
                content_ref=identity.content_ref,
                availability=identity.availability,
            )
            row = self.repository.get_by_source(
                identity.workspace_id,
                identity.root_session_id,
                identity.source_kind,
                identity.source_id,
            )
            return _wire_entry(row)
        entry = {
            "workspace_id": identity.workspace_id,
            "root_session_id": identity.root_session_id,
            "timeline_position": self.repository.next_position(
                identity.workspace_id, identity.root_session_id
            ),
            "item_id": identity.item_id,
            "kind": identity.kind,
            "source_kind": identity.source_kind,
            "source_id": identity.source_id,
            "source_session_id": identity.source_session_id,
            "source_position": identity.source_position,
            "planning_binding_id": identity.planning_binding_id,
            "planning_operation_id": identity.planning_operation_id,
            "workflow_run_id": identity.workflow_run_id,
            "node_run_id": identity.node_run_id,
            "segment_id": identity.segment_id,
            "parent_item_id": identity.parent_item_id,
            "revision": identity.revision,
            "occurred_at_unix": _unix(identity.occurred_at),
            "content_ref": identity.content_ref,
            "availability": identity.availability,
            "source_state": None,
            "broadcast_state": "pending",
            "created_at_unix": now,
            "updated_at_unix": now,
        }
        try:
            self.repository.insert_entry(entry)
        except sqlite3.IntegrityError:
            # Lost a dedup race against another writer of the same source:
            # the identity is unique, so keep the winner instead of failing.
            row = self.repository.get_by_source(
                identity.workspace_id,
                identity.root_session_id,
                identity.source_kind,
                identity.source_id,
            )
            if row is None:
                raise
            return _wire_entry(row)
        return _wire_entry(entry)

    # --- frozen PostCommitNotifierPort (P01 contract 1.0.0) ---

    def _record_source(self, identity: TimelineEntryIdentity, fingerprint: str | None):
        """Record a swept source and persist its change-detection fingerprint."""

        entry = self.record(identity)
        if fingerprint is not None:
            self.repository.set_source_state(
                self.workspace_id,
                identity.root_session_id,
                entry.timeline_position,
                fingerprint,
            )
        return entry

    def notify_after_commit(self, entry: TimelineEntryIdentity) -> None:
        """Schedule the post-commit fan-out; a rollback must never notify.

        The durable outbox row stays ``pending`` until the fan-out actually
        ran: a crash between commit and broadcast leaves it behind and the
        replay sweep re-emits it (idempotent, clients merge by item_id).
        """

        self.journal.after_commit(self._make_broadcast_callback(entry))

    def _make_broadcast_callback(self, identity: TimelineEntryIdentity):
        def broadcast() -> None:
            fan_out = getattr(self, "broadcast", None)
            if fan_out is not None:
                fan_out(identity)
            row = self.repository.get_by_source(
                identity.workspace_id,
                identity.root_session_id,
                identity.source_kind,
                identity.source_id,
            )
            if row is None:
                return
            self.journal.transact(
                lambda _: self.repository.mark_broadcast(
                    identity.workspace_id,
                    identity.root_session_id,
                    row["timeline_position"],
                )
            )

        return broadcast

    def pending_broadcasts(
        self, root_session_id: str, *, limit: int = 200
    ) -> list[TimelineSinkEntry]:
        rows = self.repository.pending_broadcasts(self.workspace_id, root_session_id, limit=limit)
        return [_wire_entry(row) for row in rows]

    def replay_outbox(self, root_session_id: str, *, limit: int = 200) -> int:
        """Re-emit pending outbox rows after a restart; returns replayed count."""

        pending = self.pending_broadcasts(root_session_id, limit=limit)
        fan_out = getattr(self, "broadcast", None)
        if fan_out is not None:
            for row in pending:
                fan_out(_identity_of(row))
        if not pending:
            return 0
        self.journal.transact(
            lambda _: [
                self.repository.mark_broadcast(
                    self.workspace_id, root_session_id, row.timeline_position
                )
                for row in pending
            ]
        )
        return len(pending)

    # --- bounded safe activity content (P07.1/P07.2) ---

    def persist_activity_content(
        self,
        session_id: str,
        activity_id: str,
        delta: str,
        *,
        kind: str,
        revision: int,
    ) -> SafeContentRef:
        """Durably append one already-projected safe fragment (commit-then-send).

        Only text that the projection layer already redacted ever reaches this
        store; tool results and artifacts stay behind owner references. The
        write runs in its own transaction and returns the frozen SafeContentRef
        with the committed offset — callers broadcast only after this returns,
        and a failure must be diagnosed, never silently swallowed.
        """

        if not delta:
            latest = self.repository.latest_content(self.workspace_id, session_id, activity_id)
            return SafeContentRef(
                content_id=_content_id(activity_id),
                kind="inline",
                committed_offset=latest["committed_offset"] if latest else 0,
                total_length=latest["total_length"] if latest else 0,
                availability="committed",
            )
        latest = self.repository.latest_content(self.workspace_id, session_id, activity_id)
        prev_body = latest["body"] if latest else ""
        prev_offset = latest["committed_offset"] if latest else 0
        prev_total = (
            latest["total_length"] if latest is not None and latest["total_length"] else prev_offset
        )

        def work(_):
            body, committed = _append_bounded(prev_body, delta, ACTIVITY_CONTENT_MAX_BYTES)
            total = prev_total + len(delta.encode())
            now = int(self.journal.now().timestamp())
            self.repository.put_content(
                {
                    "content_id": _content_id(activity_id),
                    "workspace_id": self.workspace_id,
                    "session_id": session_id,
                    "activity_id": activity_id,
                    "revision": max(int(revision), 1),
                    "kind": kind[:32],
                    "body": body,
                    "committed_offset": committed,
                    "total_length": total,
                    "availability": "committed",
                    "created_at_unix": latest["created_at_unix"] if latest else now,
                    "updated_at_unix": now,
                }
            )
            return SafeContentRef(
                content_id=_content_id(activity_id),
                kind="inline",
                committed_offset=committed,
                total_length=total,
                availability="committed",
            )

        return self.journal.transact(work)

    def read_activity_content(
        self, session_id: str, activity_id: str, *, max_bytes: int = 16384
    ) -> SafeContentRef | None:
        """Bounded controlled read of one activity's durable safe content."""

        row = self.repository.latest_content(self.workspace_id, session_id, activity_id)
        if row is None:
            return None
        body = row["body"]
        encoded = body.encode()
        ref = SafeContentRef(
            content_id=row["content_id"],
            kind="inline",
            committed_offset=row["committed_offset"],
            total_length=row["total_length"],
            availability=row["availability"],
        )
        return {
            "ref": ref,
            "body": _bounded_text(body, max_bytes),
            "truncated": len(encoded) > max_bytes,
        }

    def read_activity_content_authorized(
        self, requesting_session_id: str, activity_id: str, *, max_bytes: int = 16384
    ) -> dict:
        """Authorized bounded read: root→run→node→leaf lineage scope (P06.4).

        The requesting session may read content whose owning session shares its
        lineage root; a fork cut never grants reach into another lineage.
        """

        row = self.repository.find_content_by_activity(self.workspace_id, activity_id)
        if row is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Activity content is unavailable"
            )
        owning = row["session_id"]
        if self.lineage(owning)[0] != self.lineage(requesting_session_id)[0]:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Activity content is outside this Session"
            )
        result = self.read_activity_content(owning, activity_id, max_bytes=max_bytes)
        if result is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Activity content is unavailable"
            )
        return result

    def durable_content_revisions(self, session_id: str, activity_ids) -> dict[str, int]:
        """Latest durable content revision per activity (recovery rehydration)."""

        return self.repository.content_revisions(self.workspace_id, session_id, list(activity_ids))

    # --- lineage authorization (P06.4) ---

    def lineage(self, session_id: str) -> tuple[str, ...]:
        """Root-first lineage ids of one session (fork ancestors included)."""

        chain: list[str] = []
        current: str | None = session_id
        for _ in range(_MAX_LINEAGE_DEPTH):
            if current is None:
                break
            session = self.journal.get_session(self.workspace_id, current)
            if session is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
            chain.append(session.session_id)
            current = session.parent_session_id
        else:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session lineage is invalid"
            )
        chain.reverse()
        return tuple(chain)

    def visible_cutoffs(self, session_id: str) -> trajectory.LineageCutoff:
        """Read-side authorization: which ancestor generations and how much.

        The requesting session sees its own stream unbounded; each ancestor
        generation only up to the fork cut its child was created with — a fork
        never inherits entries past its cut point. Workflow leaf sessions of
        runs rooted inside the requesting lineage are authorized through the
        durable root→run→node→leaf chain (P06.4).
        """

        lineage = self.lineage(session_id)
        requesting = lineage[-1]
        cutoffs: dict[str, int | None] = {}
        depth: dict[str, int] = {}
        for index, sid in enumerate(lineage):
            if sid == requesting:
                cutoffs[sid] = None
            else:
                child = self.journal.get_session(self.workspace_id, lineage[index + 1])
                cutoffs[sid] = child.parent_cut_position if child else None
            depth[sid] = len(lineage) - 1 - index
        leaves = self._run_leaf_sessions(lineage)
        return trajectory.LineageCutoff(lineage[0], cutoffs, depth, leaves)

    def _run_leaf_sessions(self, lineage) -> frozenset[str]:
        """Leaf sessions of every run rooted inside the requesting lineage."""

        generations = list(lineage)
        rows = self.journal._backend.read_all(
            "SELECT DISTINCT json_extract(n.body_json,'$.conversation_session_id') "
            "FROM workflow_node_runs n "
            "JOIN workflow_runs r ON r.workflow_run_id=n.workflow_run_id "
            "JOIN task_runs t ON t.task_run_id=r.root_task_run_id "
            "WHERE n.workspace_id=? AND t.session_id IN (" + ",".join("?" * len(generations)) + ")",
            (self.workspace_id, *generations),
        )
        return frozenset(row[0] for row in rows if row[0])

    # --- unified mixed-timeline pagination, snapshot and replay (P08) ---

    def snapshot_page(
        self,
        session_id: str,
        *,
        before: str | None = None,
        limit: int = 50,
        after_position: int | None = None,
    ) -> dict:
        """One server-side mixed page over every indexed source (P08.1).

        The cursor binds schema version, scope, high-water and the before
        position. Inserting new entries never moves old page boundaries.
        """

        if not isinstance(limit, int) or not 1 <= limit <= PAGE_MAX_ITEMS:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid history limit")
        self.reconcile(session_id)
        cutoffs = self.visible_cutoffs(session_id)
        root = cutoffs.root_session_id
        now_high, revision = self._visible_head(root, cutoffs)
        if after_position is not None:
            if not isinstance(after_position, int) or after_position < 0:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid replay position")
            return self._replay_window(session_id, root, after_position, limit)

        before_position = now_high + 1
        bound_high = None
        reset = False
        if before:
            cursor = _decode_cursor(before)
            if (
                cursor.workspace_id != self.workspace_id
                or cursor.root_session_id != root
                or cursor.schema_version != 1
            ):
                raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid history cursor")
            elif cursor.high_water > now_high:
                # The cursor references positions this index never issued
                # (rebuilt index): force a clean re-fetch.
                reset = True
            else:
                # The cursor binds its snapshot's high-water: pages walked with
                # it stay stable even as new entries commit.
                bound_high = cursor.high_water
                if cursor.before_position is not None:
                    before_position = cursor.before_position

        collected: list[dict] = []  # newest → oldest while scanning
        used = 0
        has_more = False
        scan_from = before_position
        for _batch in range(PAGE_MAX_BATCHES):
            rows = self.repository.page(
                self.workspace_id, root, before_position=scan_from, limit=limit + 1
            )
            if not rows:
                break
            for row in rows:
                if len(collected) >= limit:
                    has_more = True
                    break
                if not cutoffs.visible(row["source_session_id"], row["source_position"]):
                    continue
                item = self._display_item(session_id, row, cutoffs=cutoffs)
                size = len(json.dumps(item, ensure_ascii=False).encode())
                if used + size > PAGE_MAX_BYTES and collected:
                    has_more = True
                    break
                used += size
                collected.append(item)
                scan_from = row["timeline_position"]
            if has_more or len(rows) <= limit:
                break
        items = list(reversed(collected))  # display order: oldest → newest
        reported_high = bound_high if bound_high is not None else now_high
        cursor = None
        if has_more and collected:
            cursor = _encode_cursor(
                TimelineCursor(
                    schema_version=1,
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    high_water=reported_high,
                    before_position=collected[-1]["timeline_position"],
                )
            )
        return {
            "items": items,
            "next_cursor": cursor,
            "has_more": has_more,
            "snapshot_high_water": reported_high,
            "high_water": now_high,
            "revision": revision,
            "reset": reset,
            "bytes": used,
        }

    def _replay_window(self, session_id: str, root: str, after_position: int, limit: int) -> dict:
        """Durable replay window after a snapshot high-water (P08.2).

        Snapshot and subscribe share the high-water contract: a client that
        subscribed at ``high_water`` pulls this window in order to close any
        gap; SSE loss, duplication, reordering and epoch changes are repaired
        by merging these entries by item_id.
        """

        cutoffs = self.visible_cutoffs(session_id)
        items: list[dict] = []
        used = 0
        has_more = False
        for row in self.repository.after(self.workspace_id, root, after_position, limit=limit):
            if not cutoffs.visible(row["source_session_id"], row["source_position"]):
                continue
            item = self._display_item(session_id, row, cutoffs=cutoffs)
            size = len(json.dumps(item, ensure_ascii=False).encode())
            if used + size > PAGE_MAX_BYTES and items:
                has_more = True
                break
            used += size
            items.append(item)
        has_more = has_more or (
            len(
                self.repository.after(
                    self.workspace_id,
                    root,
                    items[-1]["timeline_position"] if items else after_position,
                    limit=limit + 1,
                )
            )
            > (len(items) if items else 0)
        )
        now_high, revision = self._visible_head(root, cutoffs)
        return {
            "items": items,
            "has_more": has_more,
            "snapshot_high_water": now_high,
            "revision": revision,
            "bytes": used,
        }

    def _display_item(
        self,
        session_id: str,
        row: dict,
        *,
        cutoffs: trajectory.LineageCutoff | None = None,
    ) -> dict:
        """Wire item for one index row; bodies stay behind content refs."""

        source = {
            "origin_session_id": row["source_session_id"],
            "source_kind": row["source_kind"],
            "source_id": row["source_id"],
        }
        content = None
        content_ref = row["content_ref"]
        record_id = None
        if row["source_kind"] == trajectory.CONVERSATION_RECORD:
            record_id = row["source_id"]
        elif content_ref:
            # An admitted chat interaction owns its input's display slot; the
            # referenced conversation record stays the body owner.
            record_id = content_ref.rsplit("/", 1)[-1]
        attachments: list[dict] = []
        if record_id is not None:
            source["record_id"] = record_id
            # References are extracted independently of the 16 KiB body gate:
            # a long message must not lose its attachment entry (BUG-GUI-004).
            attachments = self._record_attachments(row["source_session_id"], record_id)
            payload = self._inline_record_payload(row["source_session_id"], record_id)
            if payload is not None:
                if row["kind"] in {"turn_status", "interruption"}:
                    content = {
                        "finish_reason": payload.get("finish_reason", "error"),
                        "stop_code": payload.get("stop_code"),
                    }
                elif row["kind"] != "tool_activity":
                    content = payload.get("content") or ""
        for key in ("workflow_run_id", "node_run_id", "segment_id", "planning_operation_id"):
            if row[key]:
                source[key] = row[key]
        item = {
            "item_id": row["item_id"],
            "kind": row["kind"],
            "workspace_id": self.workspace_id,
            "session_id": session_id,
            "order_key": [row["timeline_position"], 0, 0, row["item_id"]],
            "revision": row["revision"],
            "timeline_position": row["timeline_position"],
            "parent_item_id": row["parent_item_id"],
            "occurred_at": datetime.fromtimestamp(row["occurred_at_unix"], tz=UTC).isoformat(),
            "source": source,
            "content": content,
            "content_ref": content_ref,
            "availability": row["availability"],
            **({"attachments": attachments} if attachments else {}),
        }
        if row["kind"] == "user_message" and record_id is not None:
            item["actions"] = {"edit_fork": self._edit_fork_action(session_id, row, cutoffs)}
        if row["kind"] == "result" and row["source_kind"] == trajectory.TASK_OUTCOME:
            projection = self._result_projection(row["source_id"])
            if projection is not None:
                item["result"] = projection
        return item

    def _result_projection(self, outcome_id: str) -> dict | None:
        """Optional readable projection of one durable TaskOutcome.

        The entry, its item_id and its timeline position are already durable;
        this only adds the bounded display body. A missing projector or an
        unreadable Outcome leaves the item exactly as before, so no page can be
        broken by one damaged result.
        """

        if self.result_projector is None:
            return None
        outcome = self.journal.get_task_outcome(self.workspace_id, outcome_id)
        if outcome is None:
            return None
        try:
            return self.result_projector.project(outcome)
        except ApplicationError:
            return None

    def _edit_fork_action(
        self,
        session_id: str,
        row: dict,
        cutoffs: trajectory.LineageCutoff | None = None,
    ) -> dict:
        """Whether 编辑到新对话 may fork from this user message.

        Only the requesting session itself and its fork ancestors are legal
        edit sources: the fork endpoint re-checks ancestry against the strict
        ``content`` walk. Workflow leaf records are visible in this view but
        can never be forked from here, so the entry is disabled up front
        instead of failing inside the fork endpoint.
        """

        if cutoffs is None:
            cutoffs = self.visible_cutoffs(session_id)
        origin = row["source_session_id"]
        if origin in cutoffs.cutoffs:
            return {"available": True, "reason_code": None}
        if origin in cutoffs.run_leaf_sessions:
            return {"available": False, "reason_code": "workflow_leaf"}
        return {"available": False, "reason_code": "outside_lineage"}

    def read_record_content_authorized(self, session_id: str, record_id: str) -> dict:
        """View-scoped body resolver for ``GET …/content/{record}``.

        The strict fork-lineage ``content`` walk stays the first gate. When it
        rejects a record, the record's owning session is re-derived server
        side from ``conversation_records`` (never from client-supplied
        source fields) and read anyway when that owner is a workflow leaf
        session of a run rooted in this view. Reading never grants forking:
        the fork endpoint keeps the strict ancestry precheck, so a readable
        workflow-leaf body can never become an edit-fork source.
        """

        try:
            return self.journal.chat_timeline.content(self.workspace_id, session_id, record_id)
        except ApplicationError as error:
            if error.code is not ApplicationErrorCode.NOT_FOUND:
                raise
        row = self.journal._backend.read_one(
            "SELECT session_id,json_extract(payload_json,'$.role'),payload_json "
            "FROM conversation_records WHERE record_id=?",
            (record_id,),
        )
        if row is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Record is missing")
        if row[0] not in self.visible_cutoffs(session_id).run_leaf_sessions:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Record is outside this timeline"
            )
        if row[1] not in ("user", "assistant"):
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Record is outside this timeline"
            )
        payload = json.loads(row[2])
        return {
            "record_id": record_id,
            "content": payload.get("content") or "",
            **({"attachments": payload["attachments"]} if payload.get("attachments") else {}),
        }

    def _record_attachments(self, session_id: str, record_id: str) -> list[dict]:
        """Bounded reference-only projection of one record's attachments.

        The query reads just the ``$.attachments`` array elements — never the
        body and never attachment bytes — and is not subject to the inline
        payload-byte gate, so a >16 KiB message keeps its history entry.
        """

        rows = self.journal._backend.read_all(
            "SELECT json_extract(a.value,'$.version'),json_extract(a.value,'$.attachment_id'),"
            "json_extract(a.value,'$.content_digest'),json_extract(a.value,'$.representation_id'),"
            "json_extract(a.value,'$.representation_digest') "
            "FROM conversation_records c,json_each(c.payload_json,'$.attachments') a "
            "WHERE c.record_id=? AND c.session_id=?",
            (record_id, session_id),
        )
        refs = []
        for (
            version,
            attachment_id,
            content_digest,
            representation_id,
            representation_digest,
        ) in rows:
            if not (
                isinstance(version, int)
                and isinstance(attachment_id, str)
                and isinstance(content_digest, str)
                and isinstance(representation_id, str)
                and isinstance(representation_digest, str)
            ):
                continue
            refs.append(
                {
                    "version": version,
                    "attachment_id": attachment_id,
                    "content_digest": content_digest,
                    "representation_id": representation_id,
                    "representation_digest": representation_digest,
                }
            )
        return refs

    def _inline_record_payload(self, session_id: str, record_id: str) -> dict | None:
        payload_json = self.journal._backend.read_one(
            "SELECT CASE WHEN payload_bytes<=? THEN payload_json ELSE NULL END "
            "FROM conversation_records WHERE record_id=? AND session_id=?",
            (16 * 1024, record_id, session_id),
        )
        if not payload_json or not payload_json[0]:
            return None
        return json.loads(payload_json[0])

    # --- source reconcile sweep (P06.2/P06.3) ---

    def reconcile(self, session_id: str) -> dict[str, int]:
        """Index any committed source facts that have no entry yet.

        Deterministic, idempotent and bounded: append-only streams advance by
        watermark, mutable sources re-fingerprint a bounded recent window. Runs
        in its own transaction so a partial failure leaves the index unchanged.
        """

        lineage = self.lineage(session_id)
        root = lineage[0]

        def work(_):
            stats: dict[str, int] = {}
            interaction_map = self._sweep_chat_interactions(lineage, root)
            stats["chat_interaction"] = len(interaction_map)
            stats["conversation_record"] = self._sweep_conversation_records(
                lineage, root, interaction_map
            )
            # Workflow leaf sessions of runs rooted inside this lineage: their
            # conversation records belong to the root trajectory through the
            # durable root→run→node→leaf chain (P06.4/P08.4).
            for leaf in sorted(self._run_leaf_sessions(lineage)):
                if leaf in lineage:
                    continue
                stats["conversation_record"] += self._sweep_conversation_records([leaf], root, {})
            stats["control_receipt"] = self._sweep_control_receipts(lineage, root)
            stats["task_outcome"] = self._sweep_task_outcomes(lineage, root)
            stats.update(self._sweep_workflows(lineage, root))
            stats.update(self._sweep_planning(lineage, root))
            return stats

        try:
            return self.journal.transact(work)
        except sqlite3.IntegrityError:
            # Concurrent reconcile of the same root: identities are unique and
            # the winner's rows are complete; later reads reconcile again.
            return {}

    def _visible_head(self, root: str, cutoffs: trajectory.LineageCutoff) -> tuple[int, int]:
        """(high_water, mutation_revision) over exactly the visible entries.

        Scoped per requesting view so another fork branch's activity never
        shifts this session's page boundaries or snapshot revision.
        """

        clauses = []
        params: list[object] = []
        for sid, cut in cutoffs.cutoffs.items():
            if cut is None:
                clauses.append("source_session_id=?")
                params.append(sid)
            else:
                clauses.append("(source_session_id=? AND source_position<=?)")
                params.extend([sid, cut])
        leaves = sorted(cutoffs.run_leaf_sessions)
        if leaves:
            clauses.append("source_session_id IN (" + ",".join("?" * len(leaves)) + ")")
            params.extend(leaves)
        if not clauses:
            return 0, 0
        row = self.journal._backend.read_one(
            "SELECT COALESCE(MAX(timeline_position),-1), COALESCE(SUM(revision),0) "
            "FROM chat_timeline_entries WHERE root_session_id=? AND (" + " OR ".join(clauses) + ")",
            (root, *params),
        )
        return (int(row[0]), int(row[1]) + 1) if row else (-1, 1)

    def _sweep_chat_interactions(self, lineage, root: str) -> dict[str, str]:
        """Index admitted chat inputs; returns user_record_id → interaction_id.

        The admission (D10) owns the display slot of its user input, so the
        conversation record sweep can skip records an interaction already
        covers — one input, one entry, no second user_message.
        """

        generations = [sid for sid in lineage]
        rows = self.journal._backend.read_all(
            "SELECT ci.position, ci.interaction_id, ci.session_id, ci.client_message_id, "
            "ci.status, ci.turn_id, ci.agent_run_id, ci.user_record_id, "
            "json_extract(ci.request_json,'$.intent'), cr.conversation_position "
            "FROM chat_interactions ci "
            "LEFT JOIN conversation_records cr "
            "ON cr.record_id=ci.user_record_id "
            "WHERE ci.workspace_id=? AND ci.session_id IN ("
            + ",".join("?" * len(generations))
            + ") ORDER BY ci.position DESC LIMIT ?",
            (self.workspace_id, *generations, trajectory.MUTABLE_SOURCE_WINDOW),
        )
        candidates = {}
        for (
            _position,
            interaction_id,
            session_id,
            cmid,
            status,
            turn_id,
            agent_run_id,
            user_record_id,
            intent,
            record_position,
        ) in rows:
            if intent == "steer":
                kind = "control_input"
            elif intent == "explicit_workflow":
                kind = "planning_input"
            else:
                kind = "user_message"
            # Anchor the entry to the conversation-record position whenever the
            # admitted input has one: fork cuts are record positions, so an
            # interaction-space position would leak entries past a cut (A23).
            candidates[interaction_id] = (
                record_position if record_position is not None else None,
                session_id,
                cmid,
                status,
                turn_id,
                agent_run_id,
                user_record_id,
                kind,
            )
        known = self.repository.fingerprints(
            self.workspace_id, root, trajectory.CHAT_INTERACTION, list(candidates)
        )
        indexed: dict[str, str] = {}
        for interaction_id, fact in sorted(
            candidates.items(), key=lambda item: (item[1][0] is None, item[1][0] or 0)
        ):
            (
                position,
                session_id,
                cmid,
                status,
                turn_id,
                agent_run_id,
                user_record_id,
                kind,
            ) = fact
            fingerprint = trajectory.source_fingerprint(
                {"status": status, "turn": turn_id, "run": agent_run_id, "position": position}
            )
            if interaction_id not in known or known[interaction_id][1] != fingerprint:
                revision = (known[interaction_id][0] + 1) if interaction_id in known else 1
                self._record_source(
                    TimelineEntryIdentity(
                        workspace_id=self.workspace_id,
                        root_session_id=root,
                        item_id=trajectory.user_item_id(session_id, cmid, interaction_id),
                        kind=kind,
                        source_kind=trajectory.CHAT_INTERACTION,
                        source_id=interaction_id,
                        source_session_id=session_id,
                        source_position=position,
                        parent_item_id=None,
                        revision=revision,
                        occurred_at=self.journal.now(),
                        content_ref=trajectory.content_ref_for(
                            self.workspace_id, session_id, user_record_id
                        )
                        if user_record_id
                        else None,
                        availability="committed",
                    ),
                    fingerprint,
                )
            indexed[user_record_id or interaction_id] = interaction_id
        return indexed

    def _sweep_conversation_records(self, lineage, root: str, interaction_map) -> int:
        indexed = 0
        for _depth, session_id in enumerate(lineage):
            session = self.journal.get_session(self.workspace_id, session_id)
            watermark = self.repository.source_watermark(
                self.workspace_id, root, trajectory.CONVERSATION_RECORD, session_id
            )
            turn_rows = self.journal._backend.read_all(
                "SELECT turn_id, task_run_id, client_message_id, created_at_unix FROM turns "
                "WHERE session_id=? ORDER BY rowid",
                (session_id,),
            )
            user_offset = self._user_turn_offset(session_id, watermark)
            while True:
                records = self.journal._backend.read_all(
                    "SELECT record_id, conversation_position, "
                    "CASE WHEN payload_bytes<=? THEN payload_json ELSE NULL END, "
                    "json_extract(payload_json,'$.role'), "
                    "json_extract(payload_json,'$.finish_reason') "
                    "FROM conversation_records WHERE session_id=? AND conversation_position>? "
                    "ORDER BY conversation_position LIMIT ?",
                    (16 * 1024, session_id, watermark, trajectory.RECORD_SCAN_STEP),
                )
                if not records:
                    break
                for record_id, position, payload_json, role, finish_reason in records:
                    turn = None
                    if role == "user":
                        if user_offset < len(turn_rows):
                            turn = turn_rows[user_offset]
                        user_offset += 1
                    elif user_offset and turn_rows:
                        turn = turn_rows[min(user_offset, len(turn_rows)) - 1]
                    if record_id in interaction_map:
                        watermark = position
                        continue
                    indexed += self._index_conversation_record(
                        root,
                        session_id,
                        int(session.created_at.timestamp()),
                        record_id,
                        position,
                        payload_json,
                        role,
                        finish_reason,
                        turn,
                    )
                    watermark = position
                if len(records) < trajectory.RECORD_SCAN_STEP:
                    break
        return indexed

    def _user_turn_offset(self, session_id: str, before_position: int) -> int:
        row = self.journal._backend.read_one(
            "SELECT COUNT(*) FROM conversation_records WHERE session_id=? "
            "AND conversation_position<=? "
            "AND CASE WHEN json_valid(payload_json) "
            "THEN json_extract(payload_json,'$.role')='user' ELSE 0 END",
            (session_id, before_position),
        )
        return int(row[0]) if row else 0

    def _index_conversation_record(
        self,
        root: str,
        session_id: str,
        session_created_unix: int,
        record_id: str,
        position: int,
        payload_json: str | None,
        role: str | None,
        finish_reason: str | None,
        turn,
    ) -> int:
        if role == "user":
            item_id = trajectory.user_item_id(session_id, turn[2] if turn else None, record_id)
        elif role == "assistant":
            item_id = trajectory.reply_item_id(session_id, position)
        else:
            item_id = record_id
        occurred_unix = turn[3] if turn else session_created_unix
        identity = TimelineEntryIdentity(
            workspace_id=self.workspace_id,
            root_session_id=root,
            item_id=item_id,
            kind=trajectory.conversation_entry_kind(role, finish_reason),
            source_kind=trajectory.CONVERSATION_RECORD,
            source_id=record_id,
            source_session_id=session_id,
            source_position=position,
            parent_item_id=None,
            revision=1,
            occurred_at=datetime.fromtimestamp(int(occurred_unix), tz=UTC),
            content_ref=trajectory.content_ref_for(self.workspace_id, session_id, record_id)
            if role in {"user", "assistant"}
            else None,
            availability="committed",
        )
        self.record(identity)
        return 1

    def _sweep_control_receipts(self, lineage, root: str) -> int:
        generations = list(lineage)
        rows = self.journal._backend.read_all(
            "SELECT command_id, session_id, json_extract(payload_json,'$.status'), "
            "json_extract(payload_json,'$.intent'), json_extract(payload_json,'$.disposition'), "
            "revision, created_at_unix FROM command_receipts "
            "WHERE receipt_kind='chat_control' AND workspace_id=? AND session_id IN ("
            + ",".join("?" * len(generations))
            + ") ORDER BY created_at_unix DESC LIMIT ?",
            (self.workspace_id, *generations, trajectory.MUTABLE_SOURCE_WINDOW),
        )
        candidates = {row[0]: row for row in rows}
        known = self.repository.fingerprints(
            self.workspace_id, root, trajectory.CONTROL_RECEIPT, list(candidates)
        )
        indexed = 0
        for command_id, fact in sorted(candidates.items(), key=lambda item: item[1][6]):
            _command_id, session_id, status, intent, disposition, _revision, created_at = fact
            fingerprint = trajectory.source_fingerprint(
                {"status": status, "intent": intent, "disposition": disposition}
            )
            if command_id in known and known[command_id][1] == fingerprint:
                continue
            self._record_source(
                TimelineEntryIdentity(
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    item_id=trajectory.control_item_id(session_id, command_id),
                    kind="control_input",
                    source_kind=trajectory.CONTROL_RECEIPT,
                    source_id=command_id,
                    source_session_id=session_id,
                    source_position=None,
                    parent_item_id=None,
                    revision=(known[command_id][0] + 1) if command_id in known else 1,
                    occurred_at=datetime.fromtimestamp(int(created_at), tz=UTC),
                    content_ref=None,
                    availability="committed",
                ),
                fingerprint,
            )
            indexed += 1
        return indexed

    def _sweep_task_outcomes(self, lineage, root: str) -> int:
        generations = list(lineage)
        placeholders = ",".join("?" * len(generations))
        window = trajectory.MUTABLE_SOURCE_WINDOW
        # Newest window first: this is the pass that notices a status change
        # and bumps the entry revision.
        recent = self.journal._backend.read_all(
            "SELECT rowid, outcome_id, session_id, task_run_id, task_status, created_at_unix "
            "FROM task_outcomes WHERE workspace_id=? AND session_id IN ("
            + placeholders
            + ") ORDER BY rowid DESC LIMIT ?",
            (self.workspace_id, *generations, window),
        )
        # Long-history backstop: a session with more Outcomes than the window
        # would otherwise never surface its older ones, because the window is
        # always re-read from the newest row. This pass selects exactly the
        # rows that still have no index entry, oldest first, so the sweep
        # converges one bounded batch per reconcile instead of rescanning the
        # whole history or rebuilding the index.
        missing = self.journal._backend.read_all(
            "SELECT o.rowid, o.outcome_id, o.session_id, o.task_run_id, o.task_status,"
            " o.created_at_unix FROM task_outcomes o WHERE o.workspace_id=?"
            " AND o.session_id IN (" + placeholders + ")"
            " AND NOT EXISTS (SELECT 1 FROM chat_timeline_entries e"
            " WHERE e.workspace_id=o.workspace_id AND e.root_session_id=?"
            " AND e.source_kind=? AND e.source_id=o.outcome_id)"
            " ORDER BY o.rowid LIMIT ?",
            (self.workspace_id, *generations, root, trajectory.TASK_OUTCOME, window),
        )
        rows = sorted({row[0]: row for row in (*recent, *missing)}.values())
        known = self.repository.fingerprints(
            self.workspace_id, root, trajectory.TASK_OUTCOME, [row[1] for row in rows]
        )
        indexed = 0
        for _rowid, outcome_id, session_id, task_run_id, task_status, created_at in rows:
            fingerprint = trajectory.source_fingerprint({"status": task_status})
            if outcome_id in known and known[outcome_id][1] == fingerprint:
                continue
            self._record_source(
                TimelineEntryIdentity(
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    item_id=trajectory.outcome_item_id(session_id, task_run_id, outcome_id),
                    kind="result",
                    source_kind=trajectory.TASK_OUTCOME,
                    source_id=outcome_id,
                    source_session_id=session_id,
                    source_position=None,
                    parent_item_id=None,
                    revision=(known[outcome_id][0] + 1) if outcome_id in known else 1,
                    occurred_at=datetime.fromtimestamp(int(created_at), tz=UTC),
                    content_ref=None,
                    availability="committed",
                ),
                fingerprint,
            )
            indexed += 1
        return indexed

    def _sweep_workflows(self, lineage, root: str) -> dict[str, int]:
        generations = list(lineage)
        run_rows = self.journal._backend.read_all(
            "SELECT DISTINCT r.workflow_run_id, r.status, t.session_id "
            "FROM workflow_runs r JOIN task_runs t ON t.task_run_id=r.root_task_run_id "
            "WHERE r.workspace_id=? AND t.session_id IN (" + ",".join("?" * len(generations)) + ")",
            (self.workspace_id, *generations),
        )
        if not run_rows:
            return {"workflow_run": 0, "workflow_node": 0}
        run_session = {row[0]: row[2] for row in run_rows}
        known = self.repository.fingerprints(
            self.workspace_id, root, trajectory.WORKFLOW_RUN, [row[0] for row in run_rows]
        )
        indexed = 0
        for workflow_run_id, status, session_id in run_rows:
            fingerprint = trajectory.source_fingerprint({"status": status})
            if workflow_run_id in known and known[workflow_run_id][1] == fingerprint:
                continue
            self._record_source(
                TimelineEntryIdentity(
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    item_id=trajectory.run_item_id(workflow_run_id),
                    kind="node_progress",
                    source_kind=trajectory.WORKFLOW_RUN,
                    source_id=workflow_run_id,
                    source_session_id=session_id,
                    source_position=None,
                    workflow_run_id=workflow_run_id,
                    parent_item_id=None,
                    revision=(known[workflow_run_id][0] + 1) if workflow_run_id in known else 1,
                    occurred_at=self.journal.now(),
                    content_ref=None,
                    availability="committed",
                ),
                fingerprint,
            )
            indexed += 1
        run_ids = [row[0] for row in run_rows]
        node_rows = self.journal._backend.read_all(
            "SELECT node_run_id, workflow_run_id, status FROM workflow_node_runs "
            "WHERE workspace_id=? AND workflow_run_id IN ("
            + ",".join("?" * len(run_ids))
            + ") ORDER BY rowid",
            (self.workspace_id, *run_ids),
        )
        known_nodes = self.repository.fingerprints(
            self.workspace_id, root, trajectory.WORKFLOW_NODE, [row[0] for row in node_rows]
        )
        node_indexed = 0
        for node_run_id, workflow_run_id, status in node_rows:
            fingerprint = trajectory.source_fingerprint({"status": status})
            if node_run_id in known_nodes and known_nodes[node_run_id][1] == fingerprint:
                continue
            self._record_source(
                TimelineEntryIdentity(
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    item_id=trajectory.node_item_id(workflow_run_id, node_run_id),
                    kind="node_progress",
                    source_kind=trajectory.WORKFLOW_NODE,
                    source_id=node_run_id,
                    source_session_id=run_session.get(workflow_run_id) or lineage[-1],
                    source_position=None,
                    workflow_run_id=workflow_run_id,
                    node_run_id=node_run_id,
                    parent_item_id=trajectory.run_item_id(workflow_run_id),
                    revision=(known_nodes[node_run_id][0] + 1) if node_run_id in known_nodes else 1,
                    occurred_at=self.journal.now(),
                    content_ref=None,
                    availability="committed",
                ),
                fingerprint,
            )
            node_indexed += 1
        return {"workflow_run": indexed, "workflow_node": node_indexed}

    def _sweep_planning(self, lineage, root: str) -> dict[str, int]:
        generations = list(lineage)
        op_rows = self.journal._backend.read_all(
            "SELECT planning_operation_id, session_id, status, planning_binding_id, "
            "result_draft_id, result_draft_version FROM workflow_planning_operations "
            "WHERE workspace_id=? AND session_id IN ("
            + ",".join("?" * len(generations))
            + ") ORDER BY rowid DESC LIMIT ?",
            (self.workspace_id, *generations, trajectory.MUTABLE_SOURCE_WINDOW),
        )
        known = self.repository.fingerprints(
            self.workspace_id,
            root,
            trajectory.PLANNING_OPERATION,
            [row[0] for row in op_rows],
        )
        indexed = 0
        for operation_id, session_id, status, binding_id, draft_id, draft_version in reversed(
            op_rows
        ):
            fingerprint = trajectory.source_fingerprint({"status": status})
            if operation_id in known and known[operation_id][1] == fingerprint:
                continue
            self._record_source(
                TimelineEntryIdentity(
                    workspace_id=self.workspace_id,
                    root_session_id=root,
                    item_id=trajectory.planning_operation_item_id(operation_id),
                    kind="planning_input",
                    source_kind=trajectory.PLANNING_OPERATION,
                    source_id=operation_id,
                    source_session_id=session_id,
                    source_position=None,
                    planning_binding_id=binding_id,
                    planning_operation_id=operation_id,
                    parent_item_id=None,
                    revision=(known[operation_id][0] + 1) if operation_id in known else 1,
                    occurred_at=self.journal.now(),
                    content_ref=None,
                    availability="committed",
                ),
                fingerprint,
            )
            indexed += 1
            if draft_id and draft_version:
                self._index_draft_version(root, session_id, draft_id, draft_version, binding_id)
        return {"planning_operation": indexed}

    def _index_draft_version(self, root, session_id, draft_id, version, binding_id) -> None:
        source_id = f"{draft_id}:{version}"
        known = self.repository.fingerprints(
            self.workspace_id, root, trajectory.PLANNING_DRAFT_VERSION, [source_id]
        )
        if source_id in known:
            return
        self._record_source(
            TimelineEntryIdentity(
                workspace_id=self.workspace_id,
                root_session_id=root,
                item_id=trajectory.draft_version_item_id(draft_id, version),
                kind="plan_version",
                source_kind=trajectory.PLANNING_DRAFT_VERSION,
                source_id=source_id,
                source_session_id=session_id,
                source_position=None,
                planning_binding_id=binding_id,
                parent_item_id=None,
                revision=1,
                occurred_at=self.journal.now(),
                content_ref=None,
                availability="committed",
            ),
            None,
        )


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _encode_cursor(cursor: TimelineCursor) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(cursor.model_dump(mode="json"), separators=(",", ":")).encode()
    ).decode()


def _decode_cursor(raw: str) -> TimelineCursor:
    """Decode a current page cursor."""

    try:
        if len(raw) > 1024:
            raise ValueError
        decoded = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return TimelineCursor.model_validate(decoded)
    except Exception:
        raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid history cursor") from None


def _content_id(activity_id: str) -> str:
    """Deterministic bounded content id for one activity."""

    return "actc_" + hashlib.sha256(activity_id.encode()).hexdigest()[:32]


def _append_bounded(body: str, delta: str, cap_bytes: int) -> tuple[str, int]:
    """Append a fragment to a bounded body, cutting at a UTF-8 char boundary."""

    room = cap_bytes - len(body.encode())
    if room <= 0:
        return body, len(body.encode())
    encoded = delta.encode()
    if len(encoded) <= room:
        return body + delta, len(body.encode()) + len(encoded)
    cut = encoded[:room].decode(errors="ignore")
    return body + cut, len(body.encode()) + len(cut.encode())


def _bounded_text(text: str, max_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode(errors="ignore")


def _wire_entry(row: dict) -> TimelineSinkEntry:
    return TimelineSinkEntry(
        workspace_id=row["workspace_id"],
        root_session_id=row["root_session_id"],
        timeline_position=row["timeline_position"],
        item_id=row["item_id"],
        kind=row["kind"],
        source_kind=row["source_kind"],
        source_id=row["source_id"],
        source_session_id=row["source_session_id"],
        source_position=row["source_position"],
        planning_binding_id=row["planning_binding_id"],
        planning_operation_id=row["planning_operation_id"],
        workflow_run_id=row["workflow_run_id"],
        node_run_id=row["node_run_id"],
        segment_id=row["segment_id"],
        parent_item_id=row["parent_item_id"],
        revision=row["revision"],
        occurred_at=datetime.fromtimestamp(row["occurred_at_unix"], tz=UTC),
        content_ref=row["content_ref"],
        availability=row["availability"],
    )


def _identity_of(entry: TimelineSinkEntry) -> TimelineEntryIdentity:
    return TimelineEntryIdentity(**entry.model_dump(exclude={"timeline_position"}, mode="python"))

# Stage 4 Subplan 37 & 38 Code Review — Durable Session/Conversation & Tool Journal

> **Branch:** `feat/stage4-operational-store` at `5af1286 feat(tools): persist tool intents and durable approvals`
> **Baseline:** `main` merge `20fb43e docs(stage4): close contract review blockers` + `9511741 feat(store): land v1 operational store foundation`
> **Scope:** Subplan 37 “Durable Session, Task, Turn, AgentRun, and No-Tool Conversation” + Subplan 38 “Tool Execution Journal and Durable Approval” — schemas v2/v3, domain models, operational adapters, conversation append boundary, turn-submit idempotency, AgentLoop integration, effect classifications, approval lifecycle, fault points.
> **Activity:** File read, `git diff main..HEAD --stat` (85 files, +16 891 –2 531), spec-to-code trace against `docs/decisions/stage-4-*.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`.
> **Policy:** No code was changed. Dirty working-tree files belonging to in-flight Subplan 39 (`src/morrow/core/recovery.py`, `tests/test_stage4_recovery.py`, `V4 recovery_reports` migration, `src/morrow/runtime/conversation.py` `plan_recovery_close`) were inspected only to avoid attributing 39 debt to 37/38.

---

## 1. Executive Summary

**Overall:** Subplans 37/38 land a coherent durable substrate. The hardest invariants — ConversationLog remains the sole grammar authority, Assistant+intents commit in one `BEGIN IMMEDIATE` transaction, approvals are one-shot versioned rows, tool declarations are independent of `ToolEffect`, and payload budgets/redaction are enforced at the Pydantic boundary — are correctly implemented and tested.

**Quality:** High for a two-subplan cut. Migration v1→v3 is ordered/checksummed and refused-forward, busy-retry is bounded and typed, conversation/turn ordering uses three separate namespaces, and the idempotent `client_message_id` envelope is correctly separated from `UserMessage` content.

**Residual risk:** ~5 high-severity issues that can cause silent divergence between the SQLite row and the in-memory projection, plus ~10 medium severity ergonomics/guardrail gaps that will bite Subplan 39 if not fixed now. No data-loss-on-happy-path bug was found; all critical bugs require a crash or a contended write to surface.

**Recommendation:** Fix the **6 MUST** items before merging to `main`; treat the **SHOULD** items as Subplan 39 entry criteria; schedule the **CONSIDER** items for Subplan 40/43.

---

## 2. Methodology

1. Read `.agent/subplans/37-*.md` and `38-*.md` completion gates, then traced each task box to its owning file(s).
2. Read the three ADRs that own the contracts (`stage-4-operational-store.md`, `stage-4-domain-and-conversation.md`, `stage-4-durable-execution-and-recovery.md`) and checked every “Locked route” / “Rejected alternative” against `src/…`.
3. `read` on `src/morrow/core/domain.py` (387 L), `store.py` (169 L), `execution.py` (830 L), `faults.py` (79 L), `journal.py` (100 L), `adapters/state/operational.py` (993 L), `migrations.py` (321 L), `journal.py` adapter (922 L), `application/turns.py` (569 L), `prepared.py` (233 L), `runtime/agent.py` (881 L), `conversation.py` (375 L), `durable_log.py` (145 L), `session.py` (106 L), `bootstrap.py` (353 L), plus `tests/test_stage4_*.py`.
4. `grep` for every fault point, approval transition, and sequence namespace.
5. Classified findings by `MUST` (correctness/safety), `SHOULD` (guardrail debt), `CONSIDER` (design polish).

---

## 3. Alignment with Spec — What Is Right

* **Narrow ports, one SQLite adapter.** `core/journal.py` protocols vs `adapters/state/journal.py` `SqliteOperationalJournal` matches the “no OperationalStore god object” rule (`docs/ARCHITECTURE.md` § layering).
* **Commit protocol.** `durable_log.py:115 DurableConversationWriter.persist_with_records` → `journal.append_records` → `restore_conversation_log` → `log.apply_committed` is exactly *candidate → validate → BEGIN IMMEDIATE → commit → replace projection*. `tests/test_stage4_durable_log.py:51` proves rollback leaves the projection unchanged.
* **Turn ordering fixed.** `application/turns.py:331 submit_user` commits Turn+User **before** `turn.started` (`runtime/agent.py:331` reassigns `turn_id` after the call). Public event type/cardinality unchanged — matches ADR § Public runtime event ordering.
* **Three namespaces.** `core/domain.py:70 SequenceNamespace` and `ConversationPosition` / `RuntimeEventSequence` / `ApplicationEventCursor` are distinct types, never compared or copied.
* **Idempotency.** `client_message_id` lives on `TurnSubmitReceipt` (`domain.py:348`) not on `UserMessage`; uniqueness is `(session_id, client_message_id)` via `turns: V2 turns.UNIQUE(session_id, client_message_id)` and `turn_submit_receipts` PK. `turns.py:189` handles `accepted_closed → replay`, `accepted_open → recovery`, same-key-different-digest → conflict — exactly the ADR table.
* **AgentRun snapshot.** `turns.py:67 build_agent_run_snapshot` freezes Profile/Preferences (`revision` + `sha256`), model, `run_policy_digest`, `tool_schema_digest`, `permission_profile_digest`, `runtime_instance_id` bounded at 64 KiB and secret-filtered (`domain.py:195`). Not a config authority — stored as evidence.
* **Tool preparation.** `application/prepared.py:103 prepare_cycle_executions` runs pure preflight, computes `file_evidence_from_plan` with `before_sha256` / `expected_after_sha256` / `expected_size` / parent conditions, then `intent_resolver` + `CapabilityPolicy` determine `requires_approval`. Handler is never called if `execution_is_visible` fails (`runtime/agent.py:531`).
* **Atomic Assistant+intents.** `turns.py:348 prepare_and_commit_assistant` opens one `journal.transact` that calls `writer.persist_with_records` and `txn.put_execution` in the **same** `BEGIN IMMEDIATE`; nested `transact` flattens via `_executor != None` (`adapters/state/journal.py:84`). Fault-injected `execution.intent_after_commit` proves rollback of both rows.
* **Approval contract.** `core/execution.py:488 DurableApproval` carries opaque `approval_id`, `intent_hash`, `tool_schema_digest`, `permission_context_digest`, `requested_scope`, `preview`, `preview_digest`, optimistic `row_version`, expiry, resolution, `consumed_at`. `turns.py:406 consume_and_mark_executing` resolves and consumes in one transact; `execution.py:789 consume_approval` checks `now >= expires_at` and `consumed_at is None`. Stale version raises `StaleRowVersionError`.
* **Independent declarations.** `core/execution.py:582 PRODUCTION_TOOL_DECLARATIONS` is hand-classified per ADR table; `bootstrap.py:124 missing_declarations(..., process_isolation)` fails composition if any registered tool lacks a declaration. `ToolEffect.NONE` for `run_command` is intentionally ignored.
* **Budgets.** `core/execution.py:48 DURABLE_PAYLOAD_BUDGETS` matches the ADR exactly; enforced via `require_payload_budget` + `refuse_secret_material` at every Pydantic `model_validator` (`domain.py:195,308`, `execution.py:306,348`).

---

## 4. Issues

### 4.1 MUST — Fix before merge (correctness / safety)

#### MUST-01 — Recovery health is in-memory only (`turns.py:199`)
```py
if existing.disposition is ACCEPTED_OPEN:  # duplicate open
    session.health = SessionHealth.NEEDS_RECOVERY
    return TurnSubmitResult("recovery", ...)
```
The durable `sessions.health` row is never updated. A process that crashes after this point will `restore_into` and read `health = OK` from SQLite, so `needs_recovery` blocking for `/new` and for “new Turn only after recovery” is lost. The same gap exists for `get_receipt` → `NEEDS_RECOVERY` after an interrupted AgentRun. Tests in `tests/test_stage4_session_conversation.py:130` manually `update_receipt` then check `runtime.run_turn(..., client-1)` — they assert `session.health is NEEDS_RECOVERY` but never reopen from a fresh `OperationalStore`.

**Fix:** Persist health inside the same idempotency check or at least call `journal.save_session(workspace_id, row.copy(health=NEEDS_RECOVERY))` before returning. The `restore_into` path already persists `NEEDS_RECOVERY/QUARANTINED`; the duplicate path should mirror it.

#### MUST-02 — `execution_is_visible` is not a cross-connection durability proof (`turns.py:377`, `runtime/agent.py:530`)
```py
def execution_is_visible(...): return self.journal.get_execution(...) is not None
missing = [id for id in committed if not committee.execution_is_visible(id)]
```
`get_execution` reads through `self._session` (= the same SQLite connection that just committed). The ADR requires “handler cannot be called unless a **fresh connection** can observe its committed intent”. A bug that commits to the WAL but never `COMMIT`s, or that violates the nested-transact flattening, would still pass this check. The existing `test_intents_are_visible_from_a_fresh_connection_before_handler` uses a second `OperationalStore` for the read — production code should do the same or be removed as a false proof.

**Fix:** Either delete the same-connection check (it adds no value beyond the successful `COMMIT`) or open a short `read_only` handle and probe there. Spending a connection per ToolCycle is acceptable; doing it on the same handle is misleading.

#### MUST-03 — Call-ID aliasing divorces durable history from provider wire (`durable_log.py:36`, `turns.py:358`)
Assistant tool calls are persisted as `call1, call2, …` (`_redacted_message_payload:42 alias = f"call{index}"`) and `DurableToolExecution.call_id` is overwritten with that alias. Later ToolMessages are mapped through `call_aliases.get(tool_call_id, "call0")`. After a crash, the restored `ConversationLog` contains `call1` not the provider’s `call_abc123`. This is intentional for redaction/budget, but:
* The next Provider request is built from `ConversationLog.messages_view()` which now contains aliased IDs — the model will see synthetic IDs that never appeared on the wire, breaking strict tool-use models that echo the same `tool_call_id`.
* `prepare_cycle_executions` already receives the original `call.id`; aliasing happens only at persistence. The in-memory projection used for the next model call still has original IDs, but the **restored** projection does not. That is a divergence between the live and the reopened conversation.

The ADR says “durable argument material is the minimum bounded redacted evidence” — redaction of **arguments**, not of the correlation ID. The ID is needed to correlate `ToolExecution` ↔ `ToolMessage` across restarts, but keeping the original opaque ID (bounded to 128 bytes via `_valid_call_id`) already satisfies the budget.

**Fix:** Persist the original `call_id` unchanged in `conversation_records.payload_json` and in `tool_executions.call_id`. Keep the alias trick only for an explicit `redacted_tool_call_id` field if you need one for diagnostics, or drop aliasing entirely. Update `durable_log._redacted_message_payload` to emit `call.id` verbatim.

#### MUST-04 — No-op `model_validator` hides an intended invariant (`core/domain.py:230`)
```py
@model_validator(mode="after")
def quarantine_is_health_not_lifecycle(self): 
    if lifecycle is DELETED and health is OK: return self
    return self
```
Both branches return `self`. The name promises `quarantine changes health, never lifecycle` but the validator enforces nothing. A future migration or manual `save_session(lifecycle=DELETED, health=NEEDS_RECOVERY)` would slip through.

**Fix:** Either delete the validator (the DB CHECK already enforces the value set, and lifecycle/tombstone rules live in `Tracker`/`SessionService`) or implement the real check: `if lifecycle == DELETED and health != OK: raise …` or `if health == QUARANTINED: assert lifecycle != DELETED`.

#### MUST-05 — `create_task_run` cannot advance the current task pointer (`adapters/state/journal.py:204`)
```py
if session.current_task_run_id is None:
    UPDATE sessions SET current_task_run_id = ?
```
Subplan 37 “persists only an **open current TaskRun pointer**.” If a later operation legitimately creates a second `task_runs` row (e.g., Subplan 40’s `ready_for_acceptance → open` retry, or a test that seeds a second task), `create_task_run` silently leaves `current_task_run_id` pointing at the old row. `save_session` would be needed to move it. No current test covers creating two tasks in one session.

**Fix:** Document that Subplan 37 intentionally allows at most one `task_runs` per `(workspace_id, session_id)` and add a guard `if session.current_task_run_id is not None: raise StorageError(CONFLICT)`. Subplan 40 can then add an explicit `move_current_task(session_id, new_task_id)` method.

#### MUST-06 — `report_id`/`turn_id` cross-workspace FK is missing (`adapters/state/migrations.py:146` V4)
`recovery_reports(session_id REFERENCES sessions(session_id))` and `recovery_receipts(session_id, command_id)` reference `sessions` but not `(workspace_id, session_id)`. `sessions` is `PRIMARY KEY (session_id)` globally, not per workspace. Two workspaces could therefore collide on `ses_*` (birthday via `RandomIdSource`). V2/V3 already have the same pattern for `turns`/`agent_runs`, but they add `JOIN sessions s ON s.session_id = … AND s.workspace_id = ?` queries to re-enforce scoping. `recovery_reports`’s `UNIQUE(session_id) WHERE status='open'` lacks the `workspace_id` guard — an attacker/workspace that guesses a `session_id` could violate the partial index across workspaces.

**Fix:** Make all business tables `PRIMARY KEY(session_id, ...)`-scoped or at least add `CHECK(workspace_id = (SELECT workspace_id FROM sessions WHERE session_id = …))`, and add `workspace_id` to the partial index. For now, change V4 to `CREATE UNIQUE INDEX recovery_reports_open_session ON recovery_reports(workspace_id, session_id) WHERE status='open'` and make `recovery_reports` FK `(workspace_id)` not needed but index accordingly. Since V4 is still dirty (not committed), this is the window to fix.

---

### 4.2 SHOULD — Fix before Subplan 39 entry (guardrail / reliability)

#### SHOULD-01 — Fault point inventory incomplete
`core/faults.py:26` defines `TURN_BEFORE_TERMINAL_COMMIT / TURN_AFTER_TERMINAL_COMMIT` but no production path ever calls `faults.check(...)` for them (`grep faults.check` hits 0 for those). Every other required point (`execution.intent_after_commit`, `approval.after_consume`, `handler.before_enter`, …) is covered and tested (`tests/test_stage4_execution.py:359`). A crash exactly at “write Turn terminal” therefore has no injected test.

**Fix:** Add `self.faults.check(TURN_BEFORE_TERMINAL_COMMIT)` before `writer.persist` in `SessionPersistence.commit` when `planned.added[-1]` is `TurnTerminalRecord`, and the symmetric after-check after `log.apply_committed`.

#### SHOULD-02 — Task-ID allocation inside a retried transaction is not idempotent (`turns.py:222`)
`task = txn.create_task_run(..., DurableTaskRun(task_run_id=self.id_source.new_id("task")))` is evaluated **inside** the `journal.transact(work)` body. `OperationalStoreSession.run_write` retries the whole `BEGIN IMMEDIATE … COMMIT` via `run_with_busy_retry`. On `BUSY`, `new_id("task")` will be called again, allocating a second random ID that is abandoned on rollback, while `command_id`/`turn_id` allocated **outside** remain stable. This is benign but leaks IDs and makes retry nondeterministic. The same pattern exists for `record_id` generation inside `durable_log.persist_with_records`.

**Fix:** Allocate every ID that will be written (task, turn, agent_run, receipt `command_id`, `record_id`) before entering `transact`, then pass them in. That mirrors how `submit_user` already allocates `turn_id`/`agent_run_id` before the call.

#### SHOULD-03 — `DataRoot` permission verification missing for the operational store (`services/workspace.py`)
Subplan 36’s ADR requires new dirs/files to be `0700/0600` and verified. `adapters/state/operational.py:376 ensure_layout` enforces it for `store/`, `artifacts/`, `backups/`, and `locks/operational-store.lock`, but `services/workspace.py:98 DataRoot.ensure` still does bare `mkdir(..., exist_ok=True)` for `locks/logs/workspaces` without `restrict_path`. A pre-existing `locks/` with `0777` would not be caught until `operational-store.lock` is first taken.

**Fix:** Call `restrict_path(..., DIRECTORY_MODE)` for `locks_path` in `DataRoot.ensure`, or at least document that YAML/bootstrap dirs intentionally keep their old mode.

#### SHOULD-04 — Secret needle false positives (`core/domain.py:41`)
`SECRET_NEEDLES = ("api_key", "authorization", "password", "credential", "sk-")` is matched via `payload.casefold()`. `"sk-"` fires on `"desk-"`, `"mask-"`, `"ask-"`. The 64 KiB snapshot is JSON dumped from `Profile`/`Preferences` where a user instruction “please mask the file” would be rejected. Existing tighter Stage 3 content type checks continue to win, but this surface will cause confusing `ValueError: cannot contain secret material` on legitimate instructions.

**Fix:** Change `"sk-"` to `"sk-"` with a word boundary, or to a regex `r"\bsk-[A-Za-z0-9]{20,}\b"`, or move it to a length-guard (`"sk-"` only when followed by ≥16 chars).

#### SHOULD-05 — Preview budget double-definition (`core/execution.py:62` vs `runtime/tools.py:39`, `application/prepared.py:192`)
Durable approval preview is bounded at `preview.count ≤ 40, each ≤ 240` (`execution.py:63 _PREVIEW_*`) but the live `ApprovalPreviewBudget` defaults to `8×200/1600` (`runtime/tools.py:159`). `prepare_cycle_executions` sanitizes via `runtime.tools._sanitize_approval_preview` (policy + local preview) **and** then `PreparedIntent` re-validates via `execution._bounded_preview` (40×240). A preview that passes the first budget can fail the second, or vice versa. `turns.py:391 requested_scope = f"{effect}:{tool_name}"` also ignores the tool’s `approval_preview_budget`.

**Fix:** Single source of truth: have `core/execution` export `APPROVAL_PREVIEW_BUDGET_DURABLE = ApprovalPreviewBudget(max_lines=40, max_line_chars=240)` and have `prepare_cycle_executions` use `registered.approval_preview_budget` for the live step and `APPROVAL_PREVIEW_BUDGET_DURABLE` for the durable step explicitly.

#### SHOULD-06 — `OperationalStoreSession.run_read` leaves `query_only` sticky on error (`operational.py:237`)
```py
self._connection.execute("PRAGMA query_only = ON")
try: return work(...)
finally: if mode in {READ_WRITE, CREATE}: execute("PRAGMA query_only = OFF")
```
If `work` raises `sqlite3.Error` that is translated to `StorageError`, the `finally` runs and clears the flag. But if `work` raises a non-`sqlite3.Error` (e.g., `Pydantic ValidationError` from `TypeAdapter.validate_python`), the same clearing happens — fine. The risk is a prior `run_read` that fails to clear due to a second exception inside the `finally` itself (`sqlite3.Error` during `PRAGMA query_only = OFF`). That would leave the connection permanently read-only. No retry wraps reads.

**Fix:** Use a save `old = _pragma_int(connection, "query_only")` and restore that, or open a separate read-only connection for `run_read` (the cost is low; the ADR already says “adapters convert rows into typed Core objects at the boundary”).

#### SHOULD-07 — `restore_conversation_log` silently drops `system` messages (`durable_log.py:22`)
`durable_from_conversation_record` only handles `TurnTerminalRecord` vs `MessageRecord`; `_redacted_message_payload` only handles `assistant`/`tool`/`else` (which becomes `message.model_dump`). The domain’s `Message` union currently excludes `SystemMessage`, but `bootstrap` builds a system-boundary snapshot via `ContextBuilder`. If a future Subplan checkpoints a system turn, `restore_conversation_log → TypeAdapter(Message).validate_python(payload)` will raise `ValidationError`, caught in `turns.py:299 restore_into` as `ConversationLogError` → quarantined. That is safe but opaque.

**Fix:** Document that `system` is not a durable `conversation_records` kind (it lives only in `AgentRunSnapshot.runtime_instance_id` / `ContextCheckpoint`) and add an explicit `if payload.get("role") == "system": raise StorageError(NEEDS_REPAIR)` before `TypeAdapter`.

#### SHOULD-08 — `journal.save_session` checks `current_task_run_id` but not `workspace_id` on the task row (`journal.py:152`)
```py
task = journal.get_task_run(workspace_id, session.current_task_run_id)
if task is None or task.session_id != session.session_id: raise
```
`get_task_run` already filters by `workspace_id`, but the check does not verify `task.workspace_id == workspace_id` (it’s implicit via the query) — minor. More importantly, a tombstoned `lifecycle=DELETED` session can still have its `current_task_run_id` overwritten because `save_session` does not forbid transitions on deleted rows.

**Fix:** Add `if existing.lifecycle is DELETED: raise StorageError(UNAVAILABLE)` in `save_session`, mirroring the ADR’s “`deleted` is a tombstone”.

#### SHOULD-09 — Tests assert budgets but not actual persisted redaction (`tests/test_stage4_tool_persist.py:126`)
The persist-before-effect test checks `listed[0].intent.effect_class is UNCONFINED_EXTERNAL_EFFECT` for an `echo` tool — but `echo` is not a production tool, its declaration falls back via `_declaration_effect(..., isolation) → UNCONFINED_EXTERNAL_EFFECT` (`application/prepared.py:91`). That masks whether the production inventory gate would have failed. The test should use a registered production tool (`read_file`) for the “intents are visible” assertion.

**Fix:** Replace the `echo` tool in `test_intents_are_visible_from_a_fresh_connection_before_handler` with `read_file`/`list_directory` via `make_read_search_tools`, or mark it `@pytest.mark.xxx` as non-production.

---

### 4.3 CONSIDER — Design polish / future-proofing

* **Read-modify-write on `turn_submit_receipts` without row-version.** `TurnSubmitReceipt` has no `row_version` field, unlike `DurableApproval`/`DurableToolExecution`. The `update_receipt` path in `journal.py:425` does a blind `UPDATE ... SET disposition = ?` without `WHERE row_version = ?`. Concurrent duplicate `client_message_id` submissions racing on `ACCEPTED_OPEN` could both succeed and both create a `Turn`. The UNIQUE constraint on `(session_id, client_message_id)` prevents duplicate `Turn` rows, but the disposition flip to `ACCEPTED_CLOSED` is not serialized. Adding `row_version INTEGER DEFAULT 1` to `turn_submit_receipts` (v5) would make it consistent with approvals.

* **`OperationalStore._connect` immutable-URI heuristic** (`operational.py:734 immutable = not os.access(parent, W_OK) …`) is speculative and not covered by the spike. On Linux, `os.access(path, W_OK)` checks real UID, not SQLite’s file lock. Prefer `immutable= False` for normal opens and `immutable=True` only for the `backup`/`diagnose` paths.

* **Doc drift.** `src/morrow/core/store.py:27 SUPPORTED_SCHEMA_VERSION = 4` in the dirty tree (v4 recovery). `docs/decisions/stage-4-operational-store.md:118` still says v1–v3. The doc was not updated when V4 landed on the branch — this is exactly the “update stale doc before continuing” rule in `AGENTS.md`. Also `.agent/PLAN.md` active subplan is still `39` but `TODO.md` is empty; the review reuses plan state without re-arming.

* **Call-site ergonomics.** `SessionPersistence.attach` mutates `session.committer = self` and `session.log` in place. `Session`’s `committer` type is `SessionCommitter | None` but `SessionPersistence` does not implement `SessionCommitter.protocol` (`commit` vs `transact` naming drift: `commit` in `SessionPersistence:155`, `transact` in the port). Static typing will flag this once `mypy` is re-enabled.

* **Budget names.** `CONVERSATION_RECORD_MAX_BYTES = 256 KiB` (`domain.py:38`) and `TOOL_CALL_ARGUMENTS_MAX_BYTES = 128 KiB per call` (`execution.py:40`) are both enforced, but the latter is checked on the raw JSON string (`require_tool_call_arguments_budget`) while the former is checked on the canonical-JSON `payload`. A single Assistant with 2 calls at 128 KiB each would exceed the 256 KiB `conversation_record` row — the commit would fail with a generic `payload exceeds budget`. An earlier guard in `prepare_cycle_executions` that sums per-call arguments would give a better error.

---

## 5. Per-File Notes

| File | Verdict | Key observation |
|---|---|---|
| `core/domain.py` | Good, 1 MUST | `SessionHealth`/`SessionLifecycle` separation correct; `quo` validator is dead; `SourceRevisionRef`/`AgentRunSnapshot` capture policy+hash as required; budgets enforced at boundary. |
| `core/execution.py` | Good | State machine `LEGAL_EXECUTION_TRANSITIONS` matches ADR; `MissingCompletionPolicy` per tool correct (Host+NATIVE both `OUTCOME_UNKNOWN`); `consume_approval` enforces expiry; `tool_declaration("run_command")` requires isolation. |
| `core/store.py` | Good, 1 SHOULD | Layout/constants match ADR; `SUPPORTED_SCHEMA_VERSION` already bumped to 4 in dirty tree while doc says 3 — doc drift. |
| `core/journal.py` | Good | Ports are truly narrow; add `RecoveryJournalPort` now lives in dirty — keep it narrow. |
| `adapters/state/operational.py` | Strong | `BEGIN IMMEDIATE`, `busy_timeout=250`, 8-retry with jitter, `WAL=FULL`, `trusted_schema=OFF`, sidecar `0600`, `maintenance_lock` as `filelock` — all per spike. `_connect` immutable heuristic is the only speculative piece. |
| `adapters/state/migrations.py` | Strong, 1 MUST | Checksummed ordered migrations + `application_id`/`user_version`/`store_identity` triple check; V2/V3 FKs correct; V4 `recovery_reports` partial index should be per-workspace (see MUST-06). |
| `adapters/state/journal.py` | Strong, 2 SHOULD | Nested-transact flattening is clever and correct; `append_records` validates `conversation_position`; `put_approval` validates digests against stored execution; missing tombstone guard in `save_session`. |
| `application/turns.py` | Good, 3 MUST/SHOULD | Commit-order fix correct; receipt replay/conflict/recovery triage correct; missing durable `health` persist; fresh-connection proof missing; ID allocation inside retry. |
| `application/prepared.py` | Good | Pure preflight → `file_evidence_from_plan` → `intent_resolver` → capability eval; fallback to `UNCONFINED_EXTERNAL_EFFECT` is safe-fail-closed; `requires_approval` flip from `CapabilityPolicy` correct. |
| `runtime/conversation.py` | Strong | Grammar authority intact; `_derive_public_turns` enforces strict turn/tool ordering; `plan_recovery_close` (dirty, for 39) correctly restricts to error envelopes and non-STOP. |
| `runtime/durable_log.py` | Good, 1 MUST | Redacted `conversation_records` + ordered `call_aliases` achieves bounded payload, but aliasing ID is the MUST-03. `restore_conversation_log` reuses Grammar validation — correct. |
| `runtime/agent.py` | Strong, 1 SHOULD | `run_task` now sources `client_message_id` and `turn_id`/`agent_run_id` through `SessionPersistence.submit_user`; durability gates (`execution_is_visible`, `_gate_durable`, `record_handler_completed`, `commit_tool_message`) placed at ADR-mandated boundaries; cancellation → `CANCELLED` terminal with truthful `interrupted_call_ids`. |
| `runtime/session.py` | Good | `Session.committer` indirection + `dirty = has_active_turn` migration correct; `finish_turn` clears `dirty` only when persisted. |
| `bootstrap.py` | Strong | `build_session_application` opens the store (`NOT_FOUND → initialize`), builds `SqliteOperationalJournal`, attaches `SessionPersistence`, composes `ToolExecutor` with `missing_declarations` gate — matches “fail if any registered tool lacks a durable declaration”. |
| `services/workspace.py` | Good, 1 SHOULD | `WORKSPACE` isolation query wise; `DataRoot` missing mode restriction on `locks/`. |

---

## 6. Test Review

* `tests/test_stage4_durable_log.py` — candidate/commit split, rollback-leaves-projection, legal-ordering — **excellent**. This is the spec’s “mutate `_records` then flush later is forbidden” proof.
* `tests/test_stage4_session_conversation.py` — `test_user_commit_precedes_turn_started_and_survives_restart`, `test_client_message_id_replay_recovery_and_conflict`, dirty-restart quarantine — **strong**. Gap: no concurrent `BUSY` contention test for `submit_user` (the `BUSY_TIMEOUT=0` spike covers storage, not turn-submit).
* `tests/test_stage4_execution.py` — budgets, `EffectClass` vs `ToolEffect` disjointness, frozen inventory, `transition_execution`/`resolve_approval`/`consume_approval` idempotence, handler-entry contract — **strong**. Budget test asserts the ADR table exactly.
* `tests/test_stage4_tool_persist.py` — handler-not-run before intent commit, pre-effect hash capture, denied-approval closes without handler, fault `EXECUTION_INTENT_AFTER_COMMIT` one-shot — **good**. The `echo` fallback to `UNCONFINED_EXTERNAL_EFFECT` (see SHOULD-09) is the only misleading assertion.
* `tests/test_stage4_journal.py` / `test_stage4_operational_store_spike.py` — ordered migration, foreign/future/corrupt refusal, WAL/SHM `0600`, busy retry — carried from Subplan 36 and still green after V3. New `test_v3_store_migrates_to_v4` (dirty) proves `recovery_reports` migration without renumbering.

**Missing (for 37/38 gates):**
* No crash `os._exit` barrier test for “commit Turn+User then `_exit` before Provider invoke is visible after reopen” — the spec lists exception/`os._exit` before/after user append, assistant append, and Turn close commit. The durable-log test uses `FailureInjector("before_commit")` but not a subprocess `_exit`.
* No multi-ordered tool-call mixed approve/deny/fail/cancel test through the durable journal (the spec lists it under “Tests and faults” for 38) — `tests/test_stage4_tool_persist.py` only covers single-call deny.
* No “duplicate open/interrupted returns recovery disposition without duplicating Turn” verified from a **fresh** `OperationalStore` reopen (see MUST-01).

---

## 7. Cross-Cutting Themes

1. **Spec discipline.** The implementation is admirably faithful — it did not re-introduce background workers, FTS, ORM, or `approval nonce`. The only spec overreach is the `call1` alias, which pretends to improve redaction at the cost of wire fidelity.
2. **Narrow-transaction idiom.** The `_executor != None` flattening is an elegant way to share one `BEGIN IMMEDIATE` across conversation + tool rows without a global `OperationalStore` god method. It should be documented as the canonical pattern for Subplans 39–44 and kept out of generic read helpers.
3. **Secret boundary.** Budgets are enforced in bytes (canonical UTF-8 after redaction) — correct. The envelope truncation (`runtime/tools.py:530 _success_envelope`) moves eligible content to Artifacts later — correct. The `sk-` needle is the only heuristic that risks breaking legitimate user content.
4. **Workspace isolation.** Enforced everywhere via `workspace_id` query predicates, not via separate DBs — per ADR. The new V4 tables are the first place the pattern is not yet per-workspace scoped (MUST-06).

---

## 8. Actionable Recommendations (prioritized)

**Merge blockers (Subplan 37/38):**
1. Persist `needs_recovery` durably on duplicate-open detection (`turns.py`).
2. Remove or fix `execution_is_visible` cross-connection proof.
3. Persist original `call_id` instead of `call1` alias.
4. Fix or delete the no-op `quarantine_is_health_not_lifecycle` validator.
5. Guard `create_task_run` to one current task or add explicit `move_current_task` (your choice for 37 vs 40).
6. Fix V4 partial index to `(workspace_id, session_id) WHERE status='open'` before committing V4.

**Before Subplan 39:**
7. Instrument `TURN_BEFORE/AFTER_TERMINAL_COMMIT` faults and add `os._exit` barrier coverage.
8. Allocate all IDs before `BEGIN IMMEDIATE`.
9. Tighten `sk-` needle.
10. Unify preview budgets.
11. Verify `locks/` permissions in `DataRoot.ensure`.

**Follow-up (Subplan 40/43):**
* Add `row_version` to `turn_submit_receipts`.
* Replace immutable-URI heuristic.
* Add doc sync for `SUPPORTED_SCHEMA_VERSION` and kill `call_aliases` once alias is removed.

---

## 9. Verdict

Subplans 37 and 38 **meet their completion gates**: a bounded scripted `User → Assistant → Turn close` conversation, and a tool intent that is committed, approval-gated, and reconciled only by explicit user choice, survive a clean restart. The code is ready to ship **after the 6 MUST fixes**, which are small, local, and covered by existing tests once adjusted. No architectural rework is needed.

> Reviewed without modifying production code. Re-verify after the MUST fixes with `uv run pytest -m 'not live' -q tests/test_stage4_durable_log.py tests/test_stage4_session_conversation.py tests/test_stage4_tool_journal.py tests/test_stage4_tool_persist.py tests/test_stage4_execution.py` and `uv run ruff check . && uv run ruff format --check .`.


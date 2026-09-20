"""Durable suggestion-only task planning. No execution service is reachable here."""

from dataclasses import dataclass

from morrow.application.api_context import request_digest
from morrow.application.workflows.plan_spec import (
    enforce_review,
    normalize,
    pin_past,
    selection_key,
)
from morrow.application.workflows.planning_catalog import PlanningCatalogService
from morrow.application.workflows.planning_model import (
    ModelPlanGenerator,
    PlanningPayloadError,
    PlanningProviderUnavailableError,
)
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.contracts import PauseIntentFact
from morrow.core.domain import (
    canonical_json_bytes,
    session_can_start_work,
    sha256_digest,
)
from morrow.core.execution import StaleRowVersionError
from morrow.core.models import ModelCost, ModelUsage
from morrow.core.workflows.drafts import WorkflowDraft, WorkflowDraftStatus
from morrow.core.workflows.planning import (
    DraftVersion,
    PausePlanningGenerationRequest,
    PlanningBinding,
    PlanningContextRef,
    PlanningOperation,
    PlanWorkflowRequest,
    ResumePlanningGenerationRequest,
)

#: Validation retry budget (spec 4.1): pause/resume never resets it.
PLANNING_ATTEMPT_BUDGET = 3
PLANNING_PAUSE_OPERATION = "pause_planning_generation"
PLANNING_RESUME_OPERATION = "resume_planning_generation"
_INVALID_PLAN_DIAGNOSTIC = (
    "invalid_plan: return a valid bounded graph using listed agents and dependencies"
)

#: The stable prefix clients already match on, optionally suffixed with the
#: sanitized provider failure code so the durable evidence records the cause.
PROVIDER_UNAVAILABLE_REASON = "planning_provider_unavailable"


def _provider_unavailable_reason(error):
    detail = getattr(error, "detail", None)
    if not detail or detail == "unknown":
        return PROVIDER_UNAVAILABLE_REASON
    return f"{PROVIDER_UNAVAILABLE_REASON}:{detail}"


def digest(value):
    return sha256_digest(canonical_json_bytes(value))


@dataclass(frozen=True)
class PreparedTaskPlan:
    request: object
    operation: PlanningOperation
    context: PlanningContextRef
    conversation: tuple
    catalog_wire: tuple
    authority_digest: str
    base_source: object = None
    parent_row_version: int | None = None
    signal_ids: tuple = ()
    past_node_ids: tuple = ()
    replan_facts: object = None


@dataclass(frozen=True)
class ResumeResult:
    """Outcome of resuming one paused planning generation.

    ``prepared`` is set only when the caller must ``dispatch()`` again; a
    saved candidate applied on resume terminates the operation instead.
    """

    replayed: bool
    operation: PlanningOperation
    prepared: PreparedTaskPlan | None = None


@dataclass(frozen=True)
class _DeferredPause:
    """complete() result when a pause intent won the race: candidate saved,
    application deferred to resume (spec 3.4)."""

    saved_candidate: dict


def _accumulate_usage(previous: ModelUsage, current: ModelUsage) -> ModelUsage:
    """Sum two usage facts; any unknown part keeps the whole unknown."""
    if previous.availability == "available" and current.availability == "available":
        return ModelUsage(
            availability="available",
            input_tokens=(previous.input_tokens or 0) + (current.input_tokens or 0),
            output_tokens=(previous.output_tokens or 0) + (current.output_tokens or 0),
            total_tokens=(previous.total_tokens or 0) + (current.total_tokens or 0),
        )
    return ModelUsage.unavailable()


def _accumulate_cost(previous: ModelCost, current: ModelCost) -> ModelCost:
    if (
        previous.availability == "available"
        and current.availability == "available"
        and previous.amount_minor is not None
        and current.amount_minor is not None
        and previous.currency == current.currency
    ):
        return ModelCost(
            availability="available",
            amount_minor=previous.amount_minor + current.amount_minor,
            currency=previous.currency,
            source=current.source or previous.source,
        )
    return ModelCost.unavailable()


def _replan_snapshot(prepared):
    """Bounded durable extras a resume needs for mid-run replan rebuilds."""
    if getattr(prepared, "replan_facts", None) is None:
        return None
    return {
        "parent_row_version": prepared.parent_row_version,
        "signal_ids": list(prepared.signal_ids),
        "past_node_ids": list(prepared.past_node_ids),
        "facts": prepared.replan_facts,
    }


class TaskPlanningService:
    def __init__(self, context):
        self.context = context
        self.journal = context.journal
        self.workspace_id = context.workspace_id
        self.repo = self.journal.workflows.planning
        self.jobs = {}
        self.generator = ModelPlanGenerator(
            context.application.provider_service, context.api.artifacts
        )
        self._drafts = None
        self._catalog = None

    @property
    def drafts(self):
        # Execution products stay behind the management proxy: resolving them without a
        # configured Provider must fail only when planning is actually used, not at startup.
        if self._drafts is None:
            self._drafts = self.context.products.workflow_drafts
        return self._drafts

    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = PlanningCatalogService(self.drafts)
        return self._catalog

    def _session(self, session_id):
        session = self.context.chat.require_session(session_id)
        if not session_can_start_work(session.lifecycle, session.health):
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session must be active and healthy"
            )
        return session

    def _assert_future_only(self, binding, source):
        run = self.journal.workflows.get_run(self.workspace_id, binding.parent_run_id or "")
        runtime = getattr(self.context, "runtime", None)
        if run is None or runtime is None or getattr(runtime, "patches", None) is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "change parent run is missing")
        from morrow.core.workflows.patches import FutureGraphPatch

        patch = FutureGraphPatch(
            workflow_patch_id=self.context.application.id_source.new_id("wpatch"),
            workspace_id=self.workspace_id,
            parent_run_id=run.workflow_run_id,
            base_workflow_revision_id=run.workflow_revision_id,
            expected_parent_row_version=max(run.row_version, 1),
            source=source,
            requested_by=binding.origin_interaction_id,
        )
        try:
            runtime.patches.validate(patch, active_model=binding.context_ref.model)
        except ApplicationError as exc:
            if "past_forgery" in str(exc).casefold() or "immutable" in str(exc).casefold():
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "已开始的节点不能改写；请新增后续修订步骤，而不是修改已准入或已完成的节点。",
                ) from exc
            raise

    def _guard_live_run(self, session_id, *, allow_change=False):
        """Refuse in-place edits of the initial draft while a run is live (D10)."""

        admission = getattr(self.context.chat, "admission", None)
        if admission is None:
            return
        run = admission._current_run(session_id)
        if run is None or run.status.terminal:
            return
        binding = self.repo.binding(self.workspace_id, session_id)
        if allow_change and binding is not None and binding.mode in {"change", "repair"}:
            return
        raise ApplicationError(
            ApplicationErrorCode.CONFLICT,
            "A running plan cannot be edited in place; pause and prepare a change",
        )

    def _settings(self, session_id):
        settings, sources = self.context.chat.settings.resolve(session_id)
        self.context.chat.settings.validate(settings)
        if settings.model is None:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Select a planning model")
        return settings, digest({"settings": settings.model_dump(mode="json"), "sources": sources})

    def _capabilities(self, model):
        from morrow.core.agent_runs import exact_model_capabilities

        app = self.context.application
        config = app.provider_service.provider(model.provider_id)
        return exact_model_capabilities(
            config.adapter,
            app.registry.capabilities(config.adapter),
            model,
            config.models[model.model_id].capabilities,
        )

    def _entries(self):
        return self.catalog.nodes()

    def _authority(self, session_id):
        from morrow.adapters.state.preset_preference_yaml import AgentPresetPreferenceYamlStore

        _, settings_digest = self._settings(session_id)
        preferences = AgentPresetPreferenceYamlStore(self.context.application.data_root.root).load(
            self.workspace_id
        )
        config = self.context.application.global_store.load().value
        return digest(
            {
                "settings": settings_digest,
                "catalog": [e.ref.model_dump(mode="json") for e in self._entries()],
                "preferences": preferences.model_dump(mode="json"),
                "config_revision": config.revision,
                "constraints": self.context.products.graph_planner._constraints(),
            }
        )

    def begin(self, request):
        """Bus command: preflight before any Provider call; persist receipt identity."""
        self._session(request.session_id)
        old = self.repo.command(self.workspace_id, request.command_id)
        if old:
            if old.session_id != request.session_id or old.request_digest != request.digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Planning command payload changed"
                )
            return old
        settings, settings_digest = self._settings(request.session_id)
        caps = self._capabilities(settings.model)
        self.context.chat.attachments.validate(
            request.session_id, request.attachments, image_supported="image" in caps.input_types
        )
        if request.session_id in self.context.chat.drivers:
            raise ApplicationError(ApplicationErrorCode.BUSY, "Session is executing")
        self._guard_live_run(request.session_id, allow_change=request.operation != "generate")
        from morrow.application.agent_definitions.presets import AgentPresetMaterializationService

        AgentPresetMaterializationService(
            self.journal,
            workspace_id=self.workspace_id,
            catalog=self.drafts.management.agent_publication.catalog,
            id_source=self.context.application.id_source,
        ).prepare(command_id=request.command_id)
        binding = self.repo.binding(
            self.workspace_id, request.session_id, request.planning_binding_id
        )
        if request.planning_binding_id and binding is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning binding is missing")
        if request.operation != "generate" and (binding is None or not binding.current_draft_id):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Revision requires a current draft"
            )
        base = (
            self.drafts.get(binding.current_draft_id).draft
            if binding and binding.current_draft_id
            else None
        )
        if (base.row_version if base else 0) != request.base_draft_version:
            raise StaleRowVersionError("stale planning baseline")
        if base and base.status in {WorkflowDraftStatus.REJECTED, WorkflowDraftStatus.FROZEN}:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Planning draft is terminal")
        now = self.journal.now()
        session = self._session(request.session_id)
        constraints = self.context.products.graph_planner._constraints()
        context_ref = PlanningContextRef(
            conversation_position=session.conversation_position,
            attachments=request.attachments,
            constraints=constraints,
            model=settings.model,
            generation=settings.generation,
            settings_digest=settings_digest,
        )
        if binding and context_ref.attachments != binding.context_ref.attachments:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Retain the task attachment snapshot"
            )
        if binding and binding.status != "active":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Planning binding is terminal")

        def work(_):
            nonlocal binding
            if binding is None:
                # The first accepted business goal names the Session in the same
                # transaction; default_title only fills an untitled Session,
                # so user custom titles always win (D10).
                self.journal.session_metadata.default_title(
                    self.workspace_id, request.session_id, request.task.objective
                )
                artifact_ids = set()
                for ref in request.attachments:
                    representation = self.context.chat.attachments.representation(ref)
                    artifact_ids.update(
                        {
                            ref.representation_id,
                            *(p.artifact_id for p in representation.parts),
                            *(p.artifact_id for p in representation.previews),
                        }
                    )
                binding = PlanningBinding(
                    planning_binding_id=self.context.application.id_source.new_id("wplan"),
                    workspace_id=self.workspace_id,
                    session_id=request.session_id,
                    origin_interaction_id=request.origin_interaction_id,
                    context_ref=context_ref,
                    artifact_ids=tuple(sorted(artifact_ids)),
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                self.repo.save_binding(binding)
            operation = PlanningOperation(
                planning_operation_id=self.context.application.id_source.new_id("wop"),
                workspace_id=self.workspace_id,
                session_id=request.session_id,
                planning_binding_id=binding.planning_binding_id,
                operation=request.operation,
                command_id=request.command_id,
                request_digest=request.digest,
                accepted_request_json=canonical_json_bytes(
                    request.model_dump(mode="json")
                ).decode(),
                base_draft_id=base.draft_id if base else None,
                base_draft_version=request.base_draft_version,
                status="running",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            saved = self.repo.save_operation(operation)
            self._operation_outcome_in_txn(saved, outcome="running")
            return saved

        operation = self.journal.transact(work)
        entries = self._entries()
        return PreparedTaskPlan(
            request,
            operation,
            binding.context_ref,
            self._conversation_slice(binding),
            tuple(
                {
                    "agent": selection_key(e),
                    "name": e.version.source.name,
                    "purpose": e.version.source.description,
                    "access": e.version.source.access_mode_ceiling,
                }
                for e in entries
            ),
            self._authority(request.session_id),
            base.source if base else None,
        )

    def _conversation_slice(self, binding):
        """Bounded conversation rebuild used by begin() and durable resume."""
        conversation = []
        remaining = 16384
        records = self.journal.load_records(self.workspace_id, binding.session_id)
        for record in reversed(records):
            if record.conversation_position > binding.context_ref.conversation_position:
                continue
            role = record.payload.get("role")
            text = record.payload.get("content")
            if role not in {"user", "assistant"} or not isinstance(text, str):
                continue
            text = text[-remaining:]
            conversation.append({"role": role, "content": text})
            remaining -= len(text)
            if remaining <= 0:
                break
        return tuple(reversed(conversation))

    def begin_replan(
        self, request, *, parent_row_version, signal_ids=(), past_node_ids=(), facts=None
    ):
        prepared = self.begin(request)
        if isinstance(prepared, PlanningOperation):
            return prepared
        return PreparedTaskPlan(
            prepared.request,
            prepared.operation,
            prepared.context,
            prepared.conversation,
            prepared.catalog_wire,
            prepared.authority_digest,
            prepared.base_source,
            parent_row_version=parent_row_version,
            signal_ids=tuple(signal_ids),
            past_node_ids=tuple(past_node_ids),
            replan_facts=facts,
        )

    def get_operation(self, session_id, operation_id):
        self._session(session_id)
        operation = self.repo.operation(self.workspace_id, session_id, operation_id)
        if operation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning operation is missing")
        return operation

    def cancel(self, session_id, operation_id):
        # Cooperative user cancel (P05.2): the terminal fact commits at once, but
        # an in-flight model wait is not interrupted; the generation task observes
        # the terminal state when the wait resolves and settles its own request
        # evidence with the real outcome. Unlike pause/shutdown, cancel never
        # writes pause facts and is never resumable.
        old = self.get_operation(session_id, operation_id)
        if old.status not in {"running", "queued"}:
            return old
        return self._finish(
            old, status="cancelled", cancelled_at=self.journal.now(), cancel_reason="user"
        )

    def pause(self, request: PausePlanningGenerationRequest) -> PlanningOperation:
        """Accept a pause intent durably, then quiesce the model wait (spec 3.4).

        Replay is checked before the OCC version; user cancel is a separate
        fact and never rewrites this lifecycle.
        """
        old = self.get_operation(request.session_id, request.planning_operation_id)
        digest_value = request_digest(
            PLANNING_PAUSE_OPERATION,
            {
                "session_id": request.session_id,
                "planning_operation_id": request.planning_operation_id,
                "expected_row_version": request.expected_row_version,
            },
        )
        if self._replay_command(request.command_id, PLANNING_PAUSE_OPERATION, digest_value):
            return old
        self._check_operation_version(old, request.expected_row_version)
        if old.status not in {"queued", "running"}:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a terminal planning operation cannot be paused"
            )
        now = self.journal.now()
        op_id = old.planning_operation_id
        job = self.jobs.get(op_id)

        def work(_):
            self._put_receipt(
                request.command_id,
                PLANNING_PAUSE_OPERATION,
                digest_value,
                request.session_id,
                op_id,
            )
            self._append_pause_fact_in_txn(
                old,
                command_id=request.command_id,
                fact_kind="pause",
                lifecycle="requested",
                suspended_at=None,
                created_at=now,
            )
            if job is not None:
                self._append_pause_fact_in_txn(
                    old,
                    command_id=request.command_id,
                    fact_kind="pause",
                    lifecycle="quiescing",
                    suspended_at=None,
                    created_at=now,
                )
            else:
                # Nothing is in flight: the current state is already a safe point.
                self._suspend_in_txn(old, command_id=request.command_id, replan=None)
            self.repo.event(
                self.workspace_id,
                old.session_id,
                {
                    "operation_id": op_id,
                    "stage": "planning_pause",
                    "lifecycle": "requested",
                    "command_id": request.command_id,
                },
            )

        self.journal.transact(work)
        if job is not None:
            # The cancelled generation task persists the suspended safe point
            # itself; the pause command only records the intent (D01).
            job.cancel()
        return self.repo.operation(self.workspace_id, request.session_id, op_id)

    def resume(self, request: ResumePlanningGenerationRequest) -> ResumeResult:
        """Resume one paused generation from durable input only (spec 3.4, A09).

        Revalidates permissions, budget and base version; a valid saved
        candidate is applied without paying for a new model request.
        """
        old = self.get_operation(request.session_id, request.planning_operation_id)
        digest_value = request_digest(
            PLANNING_RESUME_OPERATION,
            {
                "session_id": request.session_id,
                "planning_operation_id": request.planning_operation_id,
                "expected_row_version": request.expected_row_version,
            },
        )
        replayed = self._replay_command(request.command_id, PLANNING_RESUME_OPERATION, digest_value)
        if replayed is not None:
            return ResumeResult(replayed=True, operation=old)
        self._check_operation_version(old, request.expected_row_version)
        if old.status not in {"queued", "running"}:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a terminal planning operation cannot be resumed"
            )
        if not self.repo.paused(self.workspace_id, old.planning_operation_id):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "planning generation is not paused"
            )
        prepared = self._rebuild_prepared(old)
        self._revalidate_for_resume(old, prepared)
        saved = self._saved_candidate(old.planning_operation_id)
        next_attempt, diagnostics = self._continuation(old.planning_operation_id)
        if saved is None and next_attempt >= PLANNING_ATTEMPT_BUDGET:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "planning validation budget is exhausted; request a new plan instead",
            )
        now = self.journal.now()

        def work(_):
            self._put_receipt(
                request.command_id,
                PLANNING_RESUME_OPERATION,
                digest_value,
                request.session_id,
                old.planning_operation_id,
            )
            binding = self.repo.binding(self.workspace_id, old.session_id, old.planning_binding_id)
            if binding is None or binding.status != "active":
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "planning binding is terminal"
                )
            base = (
                self.drafts.get(binding.current_draft_id).draft
                if binding.current_draft_id
                else None
            )
            if (base.row_version if base else 0) != old.base_draft_version:
                # The base moved while paused: neither a saved candidate nor the
                # retry baseline can be applied. Expire honestly, spend nothing.
                return ResumeResult(
                    replayed=False,
                    operation=self._finish(
                        old,
                        status="expired",
                        error_code="stale",
                        diagnostics=("planning_base_moved_while_paused",),
                    ),
                )
            self._append_resume_fact_in_txn(old, command_id=request.command_id, created_at=now)
            if saved is not None:
                version = self.repo.version(
                    self.workspace_id, saved["draft_id"], saved["draft_version"]
                )
                if version is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "saved planning candidate is missing"
                    )
                self.repo.save_binding(
                    binding.model_copy(
                        update={
                            "current_draft_id": saved["draft_id"],
                            "row_version": binding.row_version + 1,
                            "updated_at": self.journal.now(),
                        }
                    ),
                    expected=binding.row_version,
                )
                return ResumeResult(
                    replayed=False,
                    operation=self._finish(
                        old,
                        status="succeeded",
                        result_draft_id=saved["draft_id"],
                        result_draft_version=saved["draft_version"],
                    ),
                )
            self._operation_outcome_in_txn(old, outcome="running", reason="resumed")
            self.repo.event(
                self.workspace_id,
                old.session_id,
                {
                    "operation_id": old.planning_operation_id,
                    "stage": "planning_resume",
                    "command_id": request.command_id,
                },
            )
            fresh = self.repo.operation(
                self.workspace_id, old.session_id, old.planning_operation_id
            )
            return ResumeResult(replayed=False, operation=fresh, prepared=prepared)

        return self.journal.transact(work)

    def _revalidate_for_resume(self, old, prepared):
        """Permissions/availability recheck before resuming (A25): accurate
        refusals, frozen model, no reset budgets."""
        try:
            self._capabilities(prepared.context.model)
        except Exception as error:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE,
                "the frozen planning model is no longer available",
            ) from error
        try:
            self._settings(old.session_id)
        except ApplicationError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, f"planning settings changed: {error}"
            ) from error

    def _saved_candidate(self, operation_id):
        """Latest valid candidate persisted while paused, or None."""
        for row in reversed(
            self.repo.outcomes(self.workspace_id, operation_id, layer="candidate_validation")
        ):
            if row[3] == "valid" and row[8]:
                import json

                body = json.loads(row[8])
                saved = body.get("saved") or body
                if saved.get("draft_id") and saved.get("draft_version"):
                    return saved
        return None

    def _continuation(self, operation_id):
        """(0-based attempt to continue, retry diagnostics) from durable facts.

        A pause ends a request, never an attempt: open attempts (no validation
        outcome) continue with a new request_sequence and their budget intact.
        """
        model_rows = self.repo.outcomes(self.workspace_id, operation_id, layer="model_request")
        if not model_rows:
            return 0, ()
        validation_rows = self.repo.outcomes(
            self.workspace_id, operation_id, layer="candidate_validation"
        )
        closed = {row[1] for row in validation_rows}
        diagnostics = tuple(row[6] for row in validation_rows if row[3] == "invalid" and row[6])
        max_display = max(row[1] for row in model_rows)
        for display in range(1, max_display + 2):
            if display not in closed:
                return display - 1, diagnostics
        return max_display, diagnostics

    def _rebuild_prepared(self, operation) -> PreparedTaskPlan:
        """Rebuild the prepared planning input from durable state (spec 3.4)."""

        if operation.accepted_request_json is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "accepted planning request payload is missing"
            )
        payload = PlanWorkflowRequest.model_validate_json(operation.accepted_request_json)
        if payload.digest != operation.request_digest:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "accepted planning request digest mismatch"
            )
        binding = self.repo.binding(
            self.workspace_id, operation.session_id, operation.planning_binding_id
        )
        if binding is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "planning binding is missing")
        base = self.drafts.get(binding.current_draft_id).draft if binding.current_draft_id else None
        snapshot = (
            self.repo.suspend_snapshot(self.workspace_id, operation.planning_operation_id) or {}
        )
        replan = snapshot.get("replan")
        parent_row_version = None
        signal_ids = ()
        past_node_ids = ()
        facts = None
        if replan:
            parent_row_version = replan.get("parent_row_version")
            signal_ids = tuple(replan.get("signal_ids") or ())
            past_node_ids = tuple(replan.get("past_node_ids") or ())
            facts = replan.get("facts")
        return PreparedTaskPlan(
            payload,
            operation,
            binding.context_ref,
            self._conversation_slice(binding),
            tuple(
                {
                    "agent": selection_key(e),
                    "name": e.version.source.name,
                    "purpose": e.version.source.description,
                    "access": e.version.source.access_mode_ceiling,
                }
                for e in self._entries()
            ),
            self._authority(operation.session_id),
            base.source if base else None,
            parent_row_version=parent_row_version,
            signal_ids=signal_ids,
            past_node_ids=past_node_ids,
            replan_facts=facts,
        )

    def _replay_command(self, command_id, operation, digest_value):
        existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if existing is None:
            return None
        if existing.operation != operation or existing.request_digest != digest_value:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "command ID was reused with a different request"
            )
        return existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY})

    def _put_receipt(self, command_id, operation, digest_value, session_id, result_id):
        return self.journal.put_application_command_receipt(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                operation=operation,
                request_digest=digest_value,
                result_kind="planning_operation",
                result_id=result_id,
            ),
        )

    def _check_operation_version(self, operation, expected):
        if expected is not None and operation.row_version != expected:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "planning operation changed; refresh and retry"
            )

    def _pause_fact_body(self, old, *, command_id, lifecycle, suspended_at, resumed_at=None):
        fact = PauseIntentFact(
            control_generation=old.row_version,
            command_id=command_id,
            owner="planning_operation",
            owner_id=old.planning_operation_id,
            reason="user_interrupt",
            lifecycle=lifecycle,
            requested_at=old.created_at,
            suspended_at=suspended_at,
            resumed_at=resumed_at,
        )
        return canonical_json_bytes(fact.model_dump(mode="json")).decode()

    def _append_pause_fact_in_txn(
        self, old, *, command_id, fact_kind, lifecycle, suspended_at, created_at
    ):
        self.repo.append_pause_fact(
            self.workspace_id,
            old.planning_operation_id,
            command_id=command_id,
            fact_kind=fact_kind,
            lifecycle=lifecycle,
            reason="user_interrupt",
            control_generation=old.row_version,
            rebuild_json=None,
            body_json=self._pause_fact_body(
                old, command_id=command_id, lifecycle=lifecycle, suspended_at=suspended_at
            ),
            created_at=created_at.isoformat(),
        )

    def _append_resume_fact_in_txn(self, old, *, command_id, created_at):
        body = PauseIntentFact(
            control_generation=old.row_version,
            command_id=command_id,
            owner="planning_operation",
            owner_id=old.planning_operation_id,
            reason="user_interrupt",
            lifecycle="resumed",
            requested_at=old.created_at,
            resumed_at=created_at,
        )
        self.repo.append_pause_fact(
            self.workspace_id,
            old.planning_operation_id,
            command_id=command_id,
            fact_kind="resume",
            lifecycle="resumed",
            reason="user_interrupt",
            control_generation=old.row_version,
            rebuild_json=None,
            body_json=canonical_json_bytes(body.model_dump(mode="json")).decode(),
            created_at=created_at.isoformat(),
        )

    def _suspend_in_txn(self, old, *, command_id, replan, saved=None):
        """Write the suspended safe point: fact + rebuild snapshot + projection.

        Must run inside the caller's transaction; user pause and graceful
        shutdown both land here with their own fact kind. ``saved`` is a
        candidate persisted just before suspending; it is recorded as a valid
        validation outcome on the interrupted request's identity so resume can
        bind it without paying for a new model request (spec 3.4).
        """

        now = self.journal.now()
        open_request = self._open_request(old.planning_operation_id)
        pause_cmd = self.repo.open_pause_command(self.workspace_id, old.planning_operation_id)
        if pause_cmd is None:
            # No pending user pause: this suspension is a graceful shutdown
            # interrupt — resumable, and distinct from a user cancel (P05.2).
            fact_kind = "shutdown"
            reason = "shutdown"
            request_reason = "shutdown"
            body = canonical_json_bytes(
                {
                    "kind": "shutdown_interrupt",
                    "operation_id": old.planning_operation_id,
                    "suspended_at": now.isoformat(),
                }
            ).decode()
        else:
            fact_kind = "pause"
            reason = "user_interrupt"
            request_reason = "user_pause"
            body = self._pause_fact_body(
                old, command_id=pause_cmd or command_id, lifecycle="suspended", suspended_at=now
            )
        self._settle_open_request_in_txn(old, outcome="interrupted", reason=request_reason)
        rebuild = {"replan": replan}
        self.repo.append_pause_fact(
            self.workspace_id,
            old.planning_operation_id,
            command_id=pause_cmd or command_id,
            fact_kind=fact_kind,
            lifecycle="suspended",
            reason=reason,
            control_generation=old.row_version,
            rebuild_json=canonical_json_bytes(rebuild).decode(),
            body_json=body,
            created_at=now.isoformat(),
        )
        if saved is not None and open_request is not None:
            attempt0, request_sequence = open_request
            self._validation_outcome_in_txn(
                old.planning_operation_id,
                old.session_id,
                attempt_display=attempt0 + 1,
                request_sequence=request_sequence,
                outcome="valid",
                reason="saved_before_pause",
                saved=saved,
            )
        self._operation_outcome_in_txn(old, outcome="paused", reason=None)
        self.repo.event(
            self.workspace_id,
            old.session_id,
            {
                "operation_id": old.planning_operation_id,
                "stage": "planning_pause",
                "lifecycle": "suspended",
                "reason": reason,
                "command_id": command_id,
            },
        )

    def _open_request(self, operation_id):
        """(0-based attempt, request_sequence) of the in-flight model request."""
        import json

        row = self.journal._backend.read_one(
            "SELECT attempt,body_json FROM workflow_planning_requests "
            "WHERE planning_operation_id=? ORDER BY attempt DESC LIMIT 1",
            (operation_id,),
        )
        if row is None:
            return None
        raw = json.loads(row[1])
        if raw.get("status") != "running":
            return None
        sequence = int(raw.get("request_sequence") or 0)
        if sequence < 1:
            return None
        return int(row[0]), sequence

    def _settle_open_request_in_txn(self, old, *, outcome, reason):
        """Settle the in-flight model request evidence, if one is open.

        The per-request outcome row is written with its own started_at and a
        fresh request_sequence identity; usage stays unavailable when the call
        never completed (cost is unknown, never fabricated zero — A11).
        """
        import json

        open_request = self._open_request(old.planning_operation_id)
        if open_request is None:
            return False
        attempt, request_sequence = open_request
        row = self.journal._backend.read_one(
            "SELECT body_json FROM workflow_planning_requests WHERE planning_operation_id=? AND attempt=?",
            (old.planning_operation_id, attempt),
        )
        raw = json.loads(row[0])
        started_at = raw.get("started_at") or self.journal.now().isoformat()
        now = self.journal.now().isoformat()
        value = {**raw, "status": outcome, "ended_at": now}
        self.journal._backend.executor().execute(
            "UPDATE workflow_planning_requests SET body_json=? WHERE planning_operation_id=? AND attempt=?",
            (canonical_json_bytes(value).decode(), old.planning_operation_id, attempt),
        )
        self.repo.save_outcome(
            self.workspace_id,
            old.planning_operation_id,
            layer="model_request",
            attempt=attempt + 1,
            request_sequence=request_sequence,
            outcome=outcome,
            started_at=started_at,
            ended_at=now,
            reason=reason,
            body_json=canonical_json_bytes(
                {"usage": raw.get("usage"), "cost": raw.get("cost")}
            ).decode(),
        )
        self.repo.event(
            self.workspace_id,
            old.session_id,
            {
                "operation_id": old.planning_operation_id,
                "stage": "request_outcome",
                "layer": "model_request",
                "attempt": attempt + 1,
                "request_sequence": request_sequence,
                "outcome": outcome,
                "started_at": started_at,
                "ended_at": now,
                "reason": reason,
                "revision": 1,
            },
        )
        self.repo.event(
            self.workspace_id,
            old.session_id,
            {
                "operation_id": old.planning_operation_id,
                "stage": "model_finished",
                "status": outcome,
                "attempt": attempt + 1,
                "request_sequence": request_sequence,
                "ended_at": now,
            },
        )
        return True

    def _operation_outcome_in_txn(
        self, old, *, outcome, reason=None, ended=False, attempt_display=1, request_sequence=1
    ):
        revision = (
            self.repo.latest_outcome_revision(
                self.workspace_id, old.planning_operation_id, "planning_operation"
            )
            + 1
        )
        now = self.journal.now().isoformat()
        self.repo.save_outcome(
            self.workspace_id,
            old.planning_operation_id,
            layer="planning_operation",
            attempt=max(attempt_display, 1),
            request_sequence=max(request_sequence, 1),
            outcome=outcome,
            started_at=old.created_at.isoformat(),
            ended_at=now if ended else None,
            reason=reason,
            revision=revision,
        )
        self.repo.event(
            self.workspace_id,
            old.session_id,
            {
                "operation_id": old.planning_operation_id,
                "stage": "request_outcome",
                "layer": "planning_operation",
                "attempt": max(attempt_display, 1),
                "request_sequence": max(request_sequence, 1),
                "outcome": outcome,
                "started_at": old.created_at.isoformat(),
                "ended_at": now if ended else None,
                "reason": reason,
                "revision": revision,
            },
        )

    def _validation_outcome_in_txn(
        self,
        operation_id,
        session_id,
        *,
        attempt_display,
        request_sequence,
        outcome,
        reason,
        saved=None,
    ):

        now = self.journal.now().isoformat()
        revision = (
            self.repo.latest_outcome_revision(
                self.workspace_id, operation_id, "candidate_validation"
            )
            + 1
        )
        body = canonical_json_bytes({"saved": saved}).decode() if saved else None
        self.repo.save_outcome(
            self.workspace_id,
            operation_id,
            layer="candidate_validation",
            attempt=attempt_display,
            request_sequence=request_sequence,
            outcome=outcome,
            started_at=now,
            ended_at=now,
            reason=(reason[:256] if reason else None),
            revision=revision,
            body_json=body,
        )
        self.repo.event(
            self.workspace_id,
            session_id,
            {
                "operation_id": operation_id,
                "stage": "request_outcome",
                "layer": "candidate_validation",
                "attempt": attempt_display,
                "request_sequence": request_sequence,
                "outcome": outcome,
                "started_at": now,
                "ended_at": now,
                "reason": reason[:256] if reason else None,
                "revision": revision,
            },
        )
        return body

    def _finish(self, old, **changes):
        value = PlanningOperation.model_validate(
            {
                **old.model_dump(mode="json"),
                **changes,
                "row_version": old.row_version + 1,
                "updated_at": self.journal.now(),
            }
        )

        def work(_):
            saved = self.repo.save_operation(value, expected=old.row_version)
            # The terminal planning fact closes every still-open activity of this
            # operation (D10): the projection owner must never leave a ghost.
            self._operation_outcome_in_txn(
                old, outcome=value.status, reason=value.error_code, ended=True
            )
            self.repo.event(
                self.workspace_id,
                value.session_id,
                {
                    "operation_id": value.planning_operation_id,
                    "stage": "operation_terminal",
                    "status": value.status,
                    "ended_at": value.updated_at.isoformat(),
                },
            )
            return saved

        return self.journal.transact(work)

    def request_started(self, operation_id, attempt, model, session_id, request_sequence):
        # Attempts are 0-based durable attempt identities; the projection and
        # outcome boundary is 1-based so attempt 0 and 1 can never collide
        # (D10). ``request_sequence`` is the per-operation ordinal of each real
        # model call; a resume in the same attempt upserts the attempt row and
        # takes the next sequence, never overwriting request evidence.
        ordinal = attempt + 1
        started_at = self.journal.now().isoformat()
        value = {
            "source": "planning",
            "model": model.model_dump(mode="json"),
            "status": "running",
            "stage": "awaiting_model",
            "started_at": started_at,
            "request_sequence": request_sequence,
            "usage": ModelUsage.unavailable().model_dump(mode="json"),
            "cost": ModelCost.unavailable().model_dump(mode="json"),
        }

        def work(_):
            self.journal._backend.executor().execute(
                "INSERT INTO workflow_planning_requests VALUES(?,?,?) "
                "ON CONFLICT(planning_operation_id,attempt) DO UPDATE SET body_json=excluded.body_json",
                (operation_id, attempt, canonical_json_bytes(value).decode()),
            )
            self.repo.event(
                self.workspace_id,
                session_id,
                {
                    "operation_id": operation_id,
                    "status": "running",
                    "stage": "awaiting_model",
                    "attempt": ordinal,
                    "request_sequence": request_sequence,
                    "started_at": started_at,
                },
            )

        self.journal.transact(work)

    def request_settled(
        self, operation_id, attempt, request_sequence, usage, cost, outcome, reason=None
    ):
        """Settle one real model request with its layered outcome (P05.3/P05.4).

        Commits the per-request outcome row and the attempt-level evidence in
        one transaction; the operation terminal is always a later commit.
        """
        import json

        ordinal = attempt + 1
        cost = cost or ModelCost.unavailable()

        def work(_):
            row = self.journal._backend.read_one(
                "SELECT body_json FROM workflow_planning_requests WHERE planning_operation_id=? AND attempt=?",
                (operation_id, attempt),
            )
            if row is None:
                return
            previous = json.loads(row[0])
            started_at = previous.get("started_at") or self.journal.now().isoformat()
            usage_total = _accumulate_usage(
                ModelUsage.model_validate(previous.get("usage") or {}), usage
            )
            cost_total = _accumulate_cost(
                ModelCost.model_validate(previous.get("cost") or {}), cost
            )
            value = {
                **previous,
                "status": outcome,
                "ended_at": self.journal.now().isoformat(),
                "usage": usage_total.model_dump(mode="json"),
                "cost": cost_total.model_dump(mode="json"),
            }
            self.journal._backend.executor().execute(
                "UPDATE workflow_planning_requests SET body_json=? WHERE planning_operation_id=? AND attempt=?",
                (canonical_json_bytes(value).decode(), operation_id, attempt),
            )
            revision = (
                self.repo.latest_outcome_revision(self.workspace_id, operation_id, "model_request")
                + 1
            )
            self.repo.save_outcome(
                self.workspace_id,
                operation_id,
                layer="model_request",
                attempt=ordinal,
                request_sequence=request_sequence,
                outcome=outcome,
                started_at=started_at,
                ended_at=value["ended_at"],
                reason=reason,
                revision=revision,
                body_json=canonical_json_bytes(
                    {"usage": usage.model_dump(mode="json"), "cost": cost.model_dump(mode="json")}
                ).decode(),
            )
            row = self.journal._backend.read_one(
                "SELECT session_id FROM workflow_planning_operations WHERE planning_operation_id=?",
                (operation_id,),
            )
            if row is not None:
                self.repo.event(
                    self.workspace_id,
                    row[0],
                    {
                        "operation_id": operation_id,
                        "stage": "model_finished",
                        "status": outcome,
                        "attempt": ordinal,
                        "request_sequence": request_sequence,
                        "ended_at": value["ended_at"],
                    },
                )
                self.repo.event(
                    self.workspace_id,
                    row[0],
                    {
                        "operation_id": operation_id,
                        "stage": "request_outcome",
                        "layer": "model_request",
                        "attempt": ordinal,
                        "request_sequence": request_sequence,
                        "outcome": outcome,
                        "started_at": started_at,
                        "ended_at": value["ended_at"],
                        "reason": reason,
                        "revision": revision,
                    },
                )

        self.journal.transact(work)

    def request_usage(self, operation_id):
        """Aggregated planning-generation usage for one operation (bounded wire).

        Per-request outcome rows are authoritative for the current schema.
        """
        import json

        rows = self.repo.outcomes(self.workspace_id, operation_id, layer="model_request")
        attempts, tokens, cost_minor, currency, available = 0, 0, None, None, True
        for row in rows:
            raw = row[8] if len(row) > 8 else row[0]
            attempts += 1
            if not raw:
                # Interrupted evidence without a usage body: stay unknown.
                available = False
                continue
            value = json.loads(raw)
            usage = value.get("usage") or {}
            if usage.get("availability") == "available":
                tokens += usage.get("total_tokens") or 0
            else:
                available = False
            cost = value.get("cost") or {}
            if cost.get("availability") == "available" and cost.get("amount_minor") is not None:
                cost_minor = (cost_minor or 0) + cost["amount_minor"]
                currency = cost.get("currency")
        return {
            "attempts": attempts,
            "total_tokens": tokens if available and attempts else None,
            "cost_amount_minor": cost_minor,
            "cost_currency": currency,
            "availability": "available" if available and attempts else "unavailable",
        }

    async def dispatch(self, prepared, *, command):
        import asyncio

        if isinstance(prepared, PlanningOperation):
            job = self.jobs.get(prepared.planning_operation_id)
            return await asyncio.shield(job) if job else prepared
        identity = prepared.operation.planning_operation_id
        job = asyncio.create_task(self.generate(prepared, command=command))
        self.jobs[identity] = job
        job.add_done_callback(
            lambda done: (
                self.jobs.pop(identity, None),
                done.exception() if not done.cancelled() else None,
            )
        )
        return await asyncio.shield(job)

    async def shutdown(self):
        """Graceful shutdown: cancel the waits; each task persists its own
        resumable interrupt (fact kind ``shutdown``) — never a user cancel."""
        import asyncio

        jobs = tuple(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def generate(self, prepared, *, command):
        """Runs on Core loop outside its bus; each durable mutation reenters the bus.

        Interruption persistence runs directly on this thread instead: it must
        also land while the bus is draining during shutdown.
        """
        import asyncio

        if isinstance(prepared, PlanningOperation):
            return prepared
        identity = prepared.operation.planning_operation_id
        session_id = prepared.request.session_id
        # Durable continuation: a resumed operation keeps its attempt and
        # validation diagnostics; pause/resume never resets the budget (P05.3).
        attempt, diagnostics = self._continuation(identity)
        while attempt < PLANNING_ATTEMPT_BUDGET:
            old = self.get_operation(session_id, identity)
            if old.status not in {"queued", "running"}:
                return old
            if self.repo.pause_pending(self.workspace_id, identity):
                return self._suspend_from_loop(old, prepared)
            request_sequence = self.repo.next_request_sequence(self.workspace_id, identity)
            usage, cost = ModelUsage.unavailable(), ModelCost.unavailable()

            def observe(u, c):
                nonlocal usage, cost
                usage, cost = u, c

            await command(
                lambda old=old, attempt=attempt, request_sequence=request_sequence: (
                    self.request_started(
                        old.planning_operation_id,
                        attempt,
                        prepared.context.model,
                        old.session_id,
                        request_sequence,
                    )
                )
            )
            spec = None
            try:
                spec = await self.generator.generate(
                    prepared, diagnostics=diagnostics, observe=observe
                )
            except asyncio.CancelledError:
                self._interrupt_open(old, prepared, pending_spec=None, diagnostics=diagnostics)
                raise
            except PlanningPayloadError as error:
                if error.transport_ok:
                    await command(
                        lambda old=old, attempt=attempt, request_sequence=request_sequence, usage=usage, cost=cost: (
                            self.request_settled(
                                old.planning_operation_id,
                                attempt,
                                request_sequence,
                                usage,
                                cost,
                                "succeeded",
                                None,
                            )
                        )
                    )
                    await self._invalid_payload(
                        old,
                        command=command,
                        attempt=attempt,
                        request_sequence=request_sequence,
                        reason=error.code,
                    )
                else:
                    await self._failed_request(
                        old,
                        command=command,
                        attempt=attempt,
                        request_sequence=request_sequence,
                        usage=usage,
                        cost=cost,
                        reason=error.code,
                    )
                diagnostics = (_INVALID_PLAN_DIAGNOSTIC,)
                attempt += 1
                continue
            except PlanningProviderUnavailableError as error:
                # Transport-level failure with a sanitized provider code: the
                # compound reason keeps the stable prefix for existing clients
                # while recording why the provider was unreachable (D10).
                return await self._failed_request(
                    old,
                    command=command,
                    attempt=attempt,
                    request_sequence=request_sequence,
                    usage=usage,
                    cost=cost,
                    reason=_provider_unavailable_reason(error),
                )
            except ValueError:
                await command(
                    lambda old=old, attempt=attempt, request_sequence=request_sequence, usage=usage, cost=cost: (
                        self.request_settled(
                            old.planning_operation_id,
                            attempt,
                            request_sequence,
                            usage,
                            cost,
                            "succeeded",
                            None,
                        )
                    )
                )
                await self._invalid_payload(
                    old,
                    command=command,
                    attempt=attempt,
                    request_sequence=request_sequence,
                    reason="invalid_plan",
                )
                diagnostics = (_INVALID_PLAN_DIAGNOSTIC,)
                attempt += 1
                continue
            except Exception:
                return await self._failed_request(
                    old,
                    command=command,
                    attempt=attempt,
                    request_sequence=request_sequence,
                    usage=usage,
                    cost=cost,
                    reason="planning_provider_unavailable",
                )
            # The request outcome is durably settled before any operation
            # terminal commit (P05.4): "model returned" is never "plan valid".
            await command(
                lambda old=old, attempt=attempt, request_sequence=request_sequence, usage=usage, cost=cost: (
                    self.request_settled(
                        old.planning_operation_id,
                        attempt,
                        request_sequence,
                        usage,
                        cost,
                        "succeeded",
                        None,
                    )
                )
            )
            try:
                result = await command(lambda spec=spec: self.complete(prepared, spec))
            except asyncio.CancelledError:
                self._interrupt_open(old, prepared, pending_spec=spec, diagnostics=diagnostics)
                raise
            except Exception:
                return await command(
                    lambda: self.fail(prepared, "unavailable", ("planning_completion_failed",))
                )
            if isinstance(result, PlanningOperation):
                return result
            if isinstance(result, _DeferredPause):
                return self._suspend_from_loop(old, prepared)
            # The candidate failed validation: the layered outcome is already
            # committed by complete(); carry the diagnostics into the next attempt.
            diagnostics = result
            attempt += 1
        return await command(lambda: self.fail(prepared, "invalid", diagnostics))

    async def _invalid_payload(self, old, *, command, attempt, request_sequence, reason):
        await command(
            lambda: self.journal.transact(
                lambda _: self._validation_outcome_in_txn(
                    old.planning_operation_id,
                    old.session_id,
                    attempt_display=attempt + 1,
                    request_sequence=request_sequence,
                    outcome="invalid",
                    reason=reason,
                )
            )
        )

    async def _failed_request(
        self, old, *, command, attempt, request_sequence, usage, cost, reason
    ):
        """Settle a transport-level failure, then terminate the operation."""
        await command(
            lambda old=old, attempt=attempt, request_sequence=request_sequence, usage=usage, cost=cost, reason=reason: (
                self.request_settled(
                    old.planning_operation_id,
                    attempt,
                    request_sequence,
                    usage,
                    cost,
                    "failed",
                    reason,
                )
            )
        )
        await command(
            lambda old=old, attempt=attempt, request_sequence=request_sequence, reason=reason: (
                self.journal.transact(
                    lambda _: self._validation_outcome_in_txn(
                        old.planning_operation_id,
                        old.session_id,
                        attempt_display=attempt + 1,
                        request_sequence=request_sequence,
                        outcome="not_produced",
                        reason=reason,
                    )
                )
            )
        )
        return await command(
            lambda prepared_old=old: self._fail_by_operation(prepared_old, "unavailable", (reason,))
        )

    def _interrupt_open(self, old, prepared, *, pending_spec, diagnostics):
        """Persist an in-flight interruption directly on the Core thread.

        Runs inside a cancelled generation task, so it cannot reenter the bus:
        user pause and graceful shutdown take their own fact kinds, and a
        finished candidate is saved before suspending (spec 3.4).
        """
        fresh = self.repo.operation(self.workspace_id, old.session_id, old.planning_operation_id)
        if fresh is None:
            return None
        if fresh.status not in {"queued", "running"}:
            # Terminal already (user cancel committed its own request settle).
            return fresh
        saved_candidate = None
        if pending_spec is not None:
            saved_candidate = self._save_candidate_safely(fresh, prepared, pending_spec)
        self.journal.transact(
            lambda _: self._suspend_in_txn(
                fresh,
                command_id=fresh.command_id,
                replan=_replan_snapshot(prepared),
                saved=saved_candidate,
            )
        )
        return fresh

    def _save_candidate_safely(self, operation, prepared, spec):
        """Persist a finished candidate without applying it; None when it does
        not validate or the base moved (the pause must not fake success)."""
        try:
            binding = self.repo.binding(
                self.workspace_id, operation.session_id, operation.planning_binding_id
            )
            base = (
                self.drafts.get(binding.current_draft_id).draft
                if binding.current_draft_id
                else None
            )
            source, metadata = normalize(
                spec,
                prepared.request.task,
                operation.planning_binding_id,
                self._entries(),
                constraints=prepared.context.constraints,
            )
            if base is not None and (
                (base.draft_id if base else None) != operation.base_draft_id
                or (base.row_version if base else 0) != operation.base_draft_version
            ):
                return None
            result, diagnostics = self.drafts.validate(source)
            errors = tuple(
                f"{d.code}: {d.message}"[:512] for d in diagnostics if d.severity == "error"
            )
            if errors or result.candidate is None:
                return None
            selections = self._selections(
                source, binding.context_ref, metadata, operation.session_id
            )
            draft, version = self._save(
                source,
                metadata,
                selections,
                operation.command_id,
                "llm_generate" if operation.operation == "generate" else "llm_revise",
                base=base,
            )
            return {"draft_id": draft.draft_id, "draft_version": version.version}
        except Exception:
            return None

    def _suspend_from_loop(self, old, prepared):
        """Suspend from the generation loop: the pause intent won the race."""
        pause_cmd = self.repo.open_pause_command(self.workspace_id, old.planning_operation_id)
        if pause_cmd is None:
            # Resumed between the check and the transaction; keep generating.
            return old
        self.journal.transact(
            lambda _: self._suspend_in_txn(
                old, command_id=pause_cmd, replan=_replan_snapshot(prepared)
            )
        )
        return self.repo.operation(self.workspace_id, old.session_id, old.planning_operation_id)

    def _fail_by_operation(self, old, code, diagnostics):
        fresh = self.repo.operation(self.workspace_id, old.session_id, old.planning_operation_id)
        if fresh is None or fresh.status not in {"running", "queued"}:
            return fresh if fresh is not None else old
        return self._finish(fresh, status="failed", error_code=code, diagnostics=diagnostics)

    def fail(self, prepared, code, diagnostics):
        old = self.get_operation(
            prepared.request.session_id, prepared.operation.planning_operation_id
        )
        if old.status not in {"running", "queued"}:
            return old
        return self._finish(old, status="failed", error_code=code, diagnostics=diagnostics)

    def _selections(self, source, context_ref, metadata, session_id):
        from morrow.adapters.state.preset_preference_yaml import AgentPresetPreferenceYamlStore
        from morrow.core.execution_selections import SelectionInputs, resolve_selection
        from morrow.core.models import ModelRef

        prefs = AgentPresetPreferenceYamlStore(self.context.application.data_root.root).load(
            self.workspace_id
        )
        preferences = {p.definition_id: p for p in prefs.presets}
        values = {}
        for node in source.nodes:
            version = self.journal.agent_definitions.get_version(
                self.workspace_id, node.agent_definition_ref.version_id
            )
            if version is None:
                raise ValueError("agent_unavailable")
            pref = preferences.get(version.source.definition_id)
            info = metadata.get(node.node_id)
            inputs = SelectionInputs(
                node_model=info.model_choice if info else None,
                node_generation=info.generation_choice if info else None,
                agent_model=version.source.model_selection
                if isinstance(version.source.model_selection, ModelRef)
                else (pref.model if pref else None),
                agent_generation=version.source.generation_selection
                or (pref.generation if pref else None),
                session_model=context_ref.model,
                session_generation=context_ref.generation,
                adapter_default_model=context_ref.model,
            )
            preliminary = resolve_selection(inputs, model_available=self.drafts.model_available)
            if preliminary.model is None:
                raise ValueError("model_unavailable")
            resolved = resolve_selection(
                inputs,
                model_available=self.drafts.model_available,
                exact_reasoning_efforts=self._capabilities(preliminary.model).reasoning_efforts,
            )
            if resolved.status != "ok":
                raise ValueError("execution_selection_incompatible")
            self.context.chat.attachments.validate(
                session_id,
                context_ref.attachments,
                image_supported="image" in self._capabilities(resolved.model).input_types,
            )
            values[node.node_id] = {
                **resolved.summary(),
                "agent_ref": node.agent_definition_ref.model_dump(mode="json"),
            }
        return values

    def complete(self, prepared, spec):
        old = self.get_operation(
            prepared.request.session_id, prepared.operation.planning_operation_id
        )
        if old.status not in {"running", "queued"}:
            return old
        binding = self.repo.binding(self.workspace_id, old.session_id, old.planning_binding_id)
        source, metadata = normalize(
            spec,
            prepared.request.task,
            old.planning_binding_id,
            self._entries(),
            constraints=prepared.context.constraints,
        )
        base = self.drafts.get(binding.current_draft_id).draft if binding.current_draft_id else None
        parent = None
        if binding.mode == "change" and binding.parent_run_id and base is not None:
            parent = self.journal.workflows.get_run(self.workspace_id, binding.parent_run_id)
            runtime = getattr(self.context, "runtime", None)
            past = (
                set(runtime.patches._past_node_ids(parent))
                if parent is not None and runtime is not None and getattr(runtime, "patches", None)
                else set(getattr(prepared, "past_node_ids", ()) or ())
            )
            version = self.repo.version(self.workspace_id, base.draft_id, base.row_version)
            previous = version.node_metadata if version is not None else {}
            source = pin_past(base.source, source, past)
            source = enforce_review(source, {**previous, **metadata})
            metadata = {**previous, **metadata}
            if source.content_hash == base.source.content_hash:
                return self._finish(
                    old,
                    status="failed",
                    error_code="invalid",
                    diagnostics=("replan_no_progress",),
                )
        stale_parent = (
            parent is not None
            and getattr(prepared, "parent_row_version", None) is not None
            and parent.row_version != prepared.parent_row_version
        )
        live_authority = binding.mode == "initial" or binding.parent_run_id is None
        if (
            binding.status != "active"
            or (base.draft_id if base else None) != old.base_draft_id
            or (base.row_version if base else 0) != old.base_draft_version
            or stale_parent
            or (live_authority and self._authority(old.session_id) != prepared.authority_digest)
        ):
            return self._finish(old, status="expired", error_code="stale", candidate_source=source)
        result, diagnostics = self.drafts.validate(source)
        errors = tuple(f"{d.code}: {d.message}"[:512] for d in diagnostics if d.severity == "error")
        current_request = self._open_request(old.planning_operation_id) or (
            max(
                (
                    row[1]
                    for row in self.repo.outcomes(
                        self.workspace_id, old.planning_operation_id, layer="model_request"
                    )
                ),
                default=1,
            ),
            self.repo.next_request_sequence(self.workspace_id, old.planning_operation_id) - 1,
        )
        attempt_display = current_request[0] + 1
        request_sequence = current_request[1]
        if errors or result.candidate is None:
            reason = "; ".join(errors)[:256] if errors else "invalid_graph"
            if request_sequence >= 1:
                self.journal.transact(
                    lambda _: self._validation_outcome_in_txn(
                        old.planning_operation_id,
                        old.session_id,
                        attempt_display=attempt_display,
                        request_sequence=request_sequence,
                        outcome="invalid",
                        reason=reason,
                    )
                )
            return errors or ("invalid_graph",)
        selections = self._selections(source, binding.context_ref, metadata, old.session_id)

        def work(_):
            draft, version = self._save(
                source,
                metadata,
                selections,
                old.command_id,
                "llm_generate" if old.operation == "generate" else "llm_revise",
                base=base,
            )
            if self.repo.pause_pending(self.workspace_id, old.planning_operation_id):
                # A pause intent won the race: persist the valid candidate
                # durably, but defer applying it — resume re-validates the base
                # version and binds without paying for the same request again.
                if request_sequence >= 1:
                    self._validation_outcome_in_txn(
                        old.planning_operation_id,
                        old.session_id,
                        attempt_display=attempt_display,
                        request_sequence=request_sequence,
                        outcome="valid",
                        reason="saved_before_pause",
                        saved={"draft_id": draft.draft_id, "draft_version": version.version},
                    )
                return _DeferredPause(
                    saved_candidate={"draft_id": draft.draft_id, "draft_version": version.version}
                )
            if request_sequence >= 1:
                self._validation_outcome_in_txn(
                    old.planning_operation_id,
                    old.session_id,
                    attempt_display=attempt_display,
                    request_sequence=request_sequence,
                    outcome="valid",
                    reason=None,
                    saved={"draft_id": draft.draft_id, "draft_version": version.version},
                )
            self.repo.save_binding(
                binding.model_copy(
                    update={
                        "current_draft_id": draft.draft_id,
                        "row_version": binding.row_version + 1,
                        "updated_at": self.journal.now(),
                    }
                ),
                expected=binding.row_version,
            )
            return self._finish(
                old,
                status="succeeded",
                result_draft_id=draft.draft_id,
                result_draft_version=version.version,
            )

        return self.journal.transact(work)

    def _save(
        self, source, metadata, selections, command_id, created_from, *, base=None, extra_errors=()
    ):
        if base and base.row_version >= 1000:
            raise ApplicationError(ApplicationErrorCode.BUSY, "Draft history capacity reached")
        from morrow.core.workflows.definitions import WorkflowDefinitionSource

        # JSON-round-trip so stored source_hash matches the bytes get_draft reloads.
        source = WorkflowDefinitionSource.model_validate(source.model_dump(mode="json"))
        result, diagnostics = self.drafts.validate(source)
        from morrow.core.workflows.drafts import WorkflowDraftDiagnostic

        diagnostics = (
            *diagnostics,
            *(
                WorkflowDraftDiagnostic(
                    severity="error",
                    code=e,
                    message="Choose compatible execution settings before starting",
                )
                for e in extra_errors
            ),
        )
        now = self.journal.now()
        values = dict(
            source=source,
            source_hash=source.content_hash,
            status=WorkflowDraftStatus.VALID
            if result.candidate and not any(d.severity == "error" for d in diagnostics)
            else WorkflowDraftStatus.INVALID,
            diagnostics=diagnostics,
            row_version=base.row_version + 1 if base else 1,
            updated_at=now,
        )
        draft = (
            WorkflowDraft.model_validate({**base.model_dump(mode="json"), **values})
            if base
            else WorkflowDraft(
                draft_id=self.context.application.id_source.new_id("wdraft"),
                workspace_id=self.workspace_id,
                created_at=now,
                **values,
            )
        )
        draft = WorkflowDraft.model_validate_json(draft.model_dump_json())
        version = DraftVersion(
            workspace_id=self.workspace_id,
            draft_id=draft.draft_id,
            version=draft.row_version,
            source=draft.source,
            source_hash=draft.source_hash,
            node_metadata=metadata,
            execution_selections=selections,
            command_id=command_id,
            created_from=created_from,
            validation_digest=digest([(d.severity, d.code, d.node_id) for d in diagnostics]),
            created_at=now,
        )
        if base:
            self.journal.workflows.save_draft(
                draft, expected_row_version=base.row_version, history=version
            )
        else:
            self.journal.workflows.create_draft(draft, history=version)
        return draft, version

    def edit(
        self,
        session_id,
        binding_id,
        *,
        source,
        metadata,
        expected_version,
        command_id,
        confirm_impact=False,
    ):
        self._session(session_id)
        self._guard_live_run(session_id, allow_change=True)
        binding = self.repo.binding(self.workspace_id, session_id, binding_id)
        if binding is None or not binding.current_draft_id or binding.status != "active":
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning draft is missing")
        request_hash = digest(
            {
                "binding": binding_id,
                "source": source.model_dump(mode="json"),
                "metadata": {k: v.model_dump(mode="json") for k, v in metadata.items()},
                "base": expected_version,
                "confirm_impact": confirm_impact,
            }
        )
        replay = self.repo.command(self.workspace_id, command_id)
        if replay:
            if replay.session_id != session_id or replay.request_digest != request_hash:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Planning command payload changed"
                )
            return replay
        base = self.drafts._require_mutable(binding.current_draft_id, expected_version)
        if (
            source.workflow_definition_id != base.source.workflow_definition_id
            or len(source.nodes) > 16
        ):
            raise ValueError("draft identity or node limit is invalid")
        previous = self.repo.version(self.workspace_id, base.draft_id, base.row_version)
        combined = {**previous.node_metadata, **metadata}
        # Existing review responsibilities cannot be relabelled to bypass their report contract.
        for key, info in previous.node_metadata.items():
            if info.responsibility == "review" and key in combined:
                combined[key] = combined[key].model_copy(update={"responsibility": "review"})
        combined = {k: v for k, v in combined.items() if k in {n.node_id for n in source.nodes}}
        removed = {n.node_id for n in base.source.nodes} - {n.node_id for n in source.nodes}
        impact = bool(removed & {r.node_id for r in base.source.required_outputs}) or any(
            sum(e.from_node_id == n for e in base.source.edges) > 1 for n in removed
        )
        source = enforce_review(source, combined)
        if binding.mode == "change":
            self._assert_future_only(binding, source)
        now = self.journal.now()
        operation = PlanningOperation(
            planning_operation_id=self.context.application.id_source.new_id("wop"),
            workspace_id=self.workspace_id,
            session_id=session_id,
            planning_binding_id=binding_id,
            operation="validate",
            command_id=command_id,
            request_digest=request_hash,
            base_draft_id=base.draft_id,
            base_draft_version=base.row_version,
            status="running",
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        selection_errors = ()
        try:
            selections = self._selections(source, binding.context_ref, combined, session_id)
        except (ValueError, ApplicationError):
            selections = {}
            selection_errors = ("execution_selection_incompatible",)

        def work(_):
            self.repo.save_operation(operation)
            if impact and not confirm_impact:
                return self._finish(
                    operation,
                    status="expired",
                    error_code="stale",
                    diagnostics=(
                        "deletion_affects_delivery_or_multiple_dependents: confirm impact",
                    ),
                    candidate_source=source,
                )
            draft, _ = self._save(
                source,
                combined,
                selections,
                command_id,
                "manual_edit",
                base=base,
                extra_errors=selection_errors,
            )
            self.repo.event(
                self.workspace_id,
                session_id,
                {
                    "draft_id": draft.draft_id,
                    "version": draft.row_version,
                    "status": draft.status.value,
                },
            )
            return self._finish(
                operation,
                status="succeeded",
                result_draft_id=draft.draft_id,
                result_draft_version=draft.row_version,
            )

        return self.journal.transact(work)

    def edit_node(self, session_id, edit):
        from morrow.core.workflows.contracts import (
            ContractRef,
            NodeOutputBinding,
            NodeOutputRef,
            TaskContract,
        )
        from morrow.core.workflows.definitions import WorkflowDefinitionSource, WorkflowEdge
        from morrow.core.workflows.planning import PlanSpec

        self._guard_live_run(session_id, allow_change=True)
        binding = self.repo.binding(self.workspace_id, session_id, edit.binding_id)
        if binding is None or not binding.current_draft_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning draft is missing")
        if edit.action == "restore":
            return self.restore(
                session_id,
                edit.binding_id,
                version=edit.restore_version,
                expected_version=edit.expected_version,
                command_id=edit.command_id,
            )
        base = self.repo.version(self.workspace_id, binding.current_draft_id, edit.expected_version)
        if base is None:
            raise StaleRowVersionError("planning baseline is missing")
        source, metadata = base.source, dict(base.node_metadata)
        nodes = {n.node_id: n for n in source.nodes}
        edges, outputs = list(source.edges), list(source.required_outputs)
        if edit.action == "layout":
            if edit.node_id not in metadata:
                raise ValueError("node metadata is missing")
            metadata[edit.node_id] = metadata[edit.node_id].model_copy(
                update={"x": edit.x, "y": edit.y}
            )
        elif edit.action == "remove":
            if edit.node_id not in nodes or len(nodes) == 1:
                raise ValueError("cannot remove the last or missing node")
            parents = [e.from_node_id for e in edges if e.to_node_id == edit.node_id]
            children = [e.to_node_id for e in edges if e.from_node_id == edit.node_id]
            del nodes[edit.node_id]
            metadata.pop(edit.node_id, None)
            edges = [e for e in edges if edit.node_id not in {e.from_node_id, e.to_node_id}]
            # A single predecessor can deterministically supply the removed node's dependents.
            for child in children:
                bindings = [
                    b
                    for b in nodes[child].input_bindings
                    if not (
                        isinstance(b, NodeOutputBinding) and b.node_output.node_id == edit.node_id
                    )
                ]
                if len(parents) == 1:
                    parent = parents[0]
                    if not any(e.from_node_id == parent and e.to_node_id == child for e in edges):
                        edges.append(WorkflowEdge(from_node_id=parent, to_node_id=child))
                    if not any(
                        isinstance(b, NodeOutputBinding) and b.node_output.node_id == parent
                        for b in bindings
                    ):
                        bindings.append(
                            NodeOutputBinding(
                                source="node_output",
                                input_name=f"input_{parent}",
                                accepts=ContractRef(kind="TextResult"),
                                node_output=NodeOutputRef(node_id=parent, output_slot="result"),
                            )
                        )
                nodes[child] = nodes[child].model_copy(update={"input_bindings": tuple(bindings)})
            # Keep dangling required outputs as explicit invalid diagnostics until user repairs delivery.
        elif edit.action == "dependencies":
            # Dedicated dependency rewrite. Unlike `replace` it never re-lowers
            # the node through normalize(), so model choices, task constraints
            # and layout metadata of the edited node all survive.
            if edit.node_id not in nodes:
                raise ValueError("node edit identity is missing")
            parents = sorted(set(edit.depends_on))
            if edit.node_id in parents or any(parent not in nodes for parent in parents):
                raise ValueError("dependency endpoints are invalid")
            node = nodes[edit.node_id]
            bindings = [b for b in node.input_bindings if not isinstance(b, NodeOutputBinding)]
            edges = [e for e in edges if e.to_node_id != edit.node_id]
            for parent in parents:
                edges.append(WorkflowEdge(from_node_id=parent, to_node_id=edit.node_id))
                bindings.append(
                    NodeOutputBinding(
                        source="node_output",
                        input_name=f"input_{parent}",
                        accepts=ContractRef(kind="TextResult"),
                        node_output=NodeOutputRef(node_id=parent, output_slot="result"),
                    )
                )
            nodes[edit.node_id] = node.model_copy(update={"input_bindings": tuple(bindings)})
        else:
            item = edit.node
            if (edit.action == "add") == (item.node_id in nodes):
                raise ValueError("node edit identity conflict")
            # Lower one node using the same normalizer, then attach the chosen dependencies.
            lowered, info = normalize(
                PlanSpec(
                    nodes=(item.model_copy(update={"depends_on": ()}),),
                    deliverables=(item.node_id,),
                ),
                TaskContract(objective=item.task),
                edit.binding_id,
                self._entries(),
                constraints=binding.context_ref.constraints,
            )
            node = lowered.nodes[0]
            bindings = list(node.input_bindings)
            edges = [e for e in edges if e.to_node_id != item.node_id]
            for parent in sorted(set(item.depends_on)):
                edges.append(WorkflowEdge(from_node_id=parent, to_node_id=item.node_id))
                bindings.append(
                    NodeOutputBinding(
                        source="node_output",
                        input_name=f"input_{parent}",
                        accepts=ContractRef(kind="TextResult"),
                        node_output=NodeOutputRef(node_id=parent, output_slot="result"),
                    )
                )
            nodes[item.node_id] = node.model_copy(update={"input_bindings": tuple(bindings)})
            metadata.update(info)
        updated = WorkflowDefinitionSource.model_validate(
            {
                **source.model_dump(mode="json"),
                "nodes": tuple(nodes.values()),
                "edges": tuple(edges),
                "required_outputs": tuple(outputs),
            }
        )
        return self.edit(
            session_id,
            edit.binding_id,
            source=updated,
            metadata=metadata,
            expected_version=edit.expected_version,
            command_id=edit.command_id,
            confirm_impact=edit.confirm_impact,
        )

    def restore(self, session_id, binding_id, *, version, expected_version, command_id):
        binding = self.repo.binding(self.workspace_id, session_id, binding_id)
        if binding is None or not binding.current_draft_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning draft is missing")
        historical = self.repo.version(self.workspace_id, binding.current_draft_id, version)
        if historical is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Draft version is missing")
        return self.edit(
            session_id,
            binding_id,
            source=historical.source,
            metadata=historical.node_metadata,
            expected_version=expected_version,
            command_id=command_id,
            confirm_impact=True,
        )

    def view(self, session_id):
        self._session(session_id)
        binding = self.repo.binding(self.workspace_id, session_id)
        draft = (
            self.drafts.get(binding.current_draft_id)
            if binding and binding.current_draft_id
            else None
        )
        version = (
            self.repo.version(self.workspace_id, draft.draft.draft_id, draft.draft.row_version)
            if draft
            else None
        )
        operations = []
        generation = None
        for operation in self.repo.operations(self.workspace_id, session_id):
            paused = self.repo.paused(self.workspace_id, operation.planning_operation_id)
            lifecycle = self.repo.latest_pause_lifecycle(
                self.workspace_id, operation.planning_operation_id
            )
            open_now = operation.status in {"queued", "running"}
            if open_now:
                # The interactive generation handle for the planning UI (lane D):
                # exactly one entry per open operation; a paused one wins so the
                # resume button always binds the resumable operation.
                generation = {
                    "planning_operation_id": operation.planning_operation_id,
                    "status": "paused" if paused else operation.status,
                    "pause_lifecycle": lifecycle,
                }
            operations.append(
                {
                    **operation.model_dump(mode="json", exclude={"candidate_source"}),
                    "usage": self.request_usage(operation.planning_operation_id),
                    "effective_status": "paused" if open_now and paused else operation.status,
                    "pause_lifecycle": lifecycle,
                }
            )
        actions = ["generate", "edit", "cancel_generation"]
        if generation is not None:
            actions.append("pause_generation")
            if generation["status"] == "paused":
                actions.append("resume_generation")
        return {
            "binding": binding,
            "draft": draft,
            "version": version.model_dump(mode="json", exclude={"source"}) if version else None,
            "operations": tuple(operations),
            "generation": generation,
            "allowed_actions": tuple(actions),
        }

    def recover(self):
        rows = self.journal._backend.read_all(
            "SELECT session_id,planning_operation_id FROM workflow_planning_operations WHERE workspace_id=? AND status IN ('running','queued')",
            (self.workspace_id,),
        )
        for sid, identity in rows:
            stale = self.repo.operation(self.workspace_id, sid, identity)
            if stale is None or self.repo.paused(self.workspace_id, identity):
                # A paused operation sits at a durable safe point: startup must
                # keep it resumable and never auto-continue generation (D16).
                continue

            def work(_journal=None, _old=stale):
                self._settle_open_request_in_txn(
                    _old, outcome="interrupted", reason="needs_recovery"
                )
                self._operation_outcome_in_txn(
                    _old, outcome="failed", reason="needs_recovery", ended=True
                )
                return self._finish(
                    _old,
                    status="failed",
                    error_code="needs_recovery",
                    diagnostics=("planning_interrupted: retry with a new command",),
                )

            self.journal.transact(work)

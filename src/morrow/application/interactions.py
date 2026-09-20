"""Shared structured interaction entry; execution stays in SessionOrchestrator."""

import asyncio

from morrow.application.task_continuity import chat_continuation_point
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, session_can_start_work, sha256_digest
from morrow.core.interactions import InteractionRequest
from morrow.core.models import AgentEvent, ChatSettings, GenerationOptions, ModelRef
from morrow.core.runtime_control import RuntimeControlKind


def provider_binding_digest(provider, model):
    """Only execution-relevant facts; tests, unrelated models and defaults are independent."""
    return sha256_digest(
        canonical_json_bytes(
            {
                "adapter": provider.adapter,
                "endpoint": provider.base_url,
                "credential_ref": provider.credential_ref.model_dump(mode="json")
                if provider.credential_ref
                else None,
                "model": provider.models[model.model_id].model_dump(mode="json"),
            }
        )
    )


class InteractionService:
    @staticmethod
    async def stream(orchestrator, request: InteractionRequest):
        async for event in orchestrator.stream(
            request.text or "[附件输入]",
            client_message_id=request.client_message_id,
            interpret_commands=False,
        ):
            yield event

    def __init__(self, manager, application, journal, workspace_id):
        self.manager = manager
        self.application = application
        self.journal = journal
        self.workspace_id = workspace_id
        self.records = journal.interactions
        self.user_stops = set()
        self.on_event = lambda sid, event: None
        self.on_change = lambda sid: None
        self.on_admit = lambda sid: None
        self.check_admission = lambda: None
        self.records.pause_on_restart(workspace_id)

    def _get(self, session_id, key):
        self.manager.require_session(session_id)
        entry = self.records.get(self.workspace_id, session_id, key)
        if entry is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Interaction is missing")
        return entry

    def receipt(self, session_id, key, *, replay=False):
        entry = self._get(session_id, key)
        result = {
            name: entry[name]
            for name in (
                "interaction_id",
                "workspace_id",
                "session_id",
                "client_message_id",
                "binding_digest",
                "status",
                "revision",
                "turn_id",
                "agent_run_id",
                "user_record_id",
                "reason",
            )
        }
        result["disposition"] = "replay" if replay else "accepted"
        if entry["request"]["intent"] == "explicit_workflow":
            run = self.manager.workflows.run(entry)
            result["workflow_run_id"] = run.workflow_run_id if run else None
            result["task_run_id"] = run.root_task_run_id if run else None
        result["queue_position"] = next(
            (
                i + 1
                for i, item in enumerate(self.records.pending(self.workspace_id, session_id))
                if item["interaction_id"] == entry["interaction_id"]
            ),
            None,
        )
        if entry["agent_run_id"]:
            terminal = self.journal.get_agent_run_terminal_metrics(
                self.workspace_id, entry["agent_run_id"]
            )
            result["run_status"] = (
                terminal.finish_reason.value
                if terminal
                else (
                    self.records.terminal_reason(entry)
                    or (
                        "running"
                        if self.active_run(session_id) == entry["agent_run_id"]
                        else "needs_recovery"
                    )
                )
            )
        else:
            result["run_status"] = None
        return result

    def _binding(self, session_id, settings):
        loaded = self.application.global_store.load()
        config = loaded.value
        effective, sources = self.manager.settings.resolve(session_id, settings)
        self.manager.settings.validate(effective)
        if config is None or effective.model is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE,
                "Configure a Provider and active model before sending",
            )
        model = effective.model
        provider = config.providers.get(model.provider_id)
        if provider is None or model.model_id not in provider.models:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Configured model is unavailable"
            )
        return {
            "binding_version": 2,
            "model": model.model_dump(mode="json"),
            "config_revision": loaded.revision,
            "settings": effective.model_dump(mode="json"),
            "settings_sources": sources,
            "provider_digest": provider_binding_digest(provider, model),
        }

    def _validate_attachments(self, session_id, request, binding):
        from morrow.core.agent_runs import exact_model_capabilities

        model = ModelRef.model_validate(binding["model"])
        config = self.application.global_store.load().value.providers[model.provider_id]
        exact = exact_model_capabilities(
            config.adapter,
            self.application.registry.capabilities(config.adapter),
            model,
            config.models[model.model_id].capabilities,
        )
        self.manager.attachments.validate_history(
            session_id, image_supported="image" in exact.input_types
        )
        return self.manager.attachments.validate(
            session_id, request.attachments, image_supported="image" in exact.input_types
        )

    def prepare_options(self, session_id, key):
        binding = self._get(session_id, key)["binding"]
        model = ModelRef.model_validate(binding["model"])
        config = self.application.global_store.load().value
        provider = config.providers.get(model.provider_id) if config else None
        if (
            provider is None
            or model.model_id not in provider.models
            or provider_binding_digest(provider, model) != binding["provider_digest"]
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "Bound model configuration changed; withdraw and send a new input",
            )
        request = InteractionRequest.model_validate(self._get(session_id, key)["request"])
        self._validate_attachments(session_id, request, binding)
        settings = binding["settings"]
        self.manager.settings.validate(ChatSettings.model_validate({**settings, "model": model}))
        if not self.application.provider_service._read_credential(
            model.provider_id, provider.credential_ref
        ):
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "绑定的 Provider 凭据已不可用")
        point = self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="chat_turn", owner_id=session_id
        )
        resume_snapshot = None
        if point is not None and point.continuation_command_id == key and point.safety:
            prior = self.journal.get_agent_run(
                self.workspace_id, point.safety.interrupted_agent_run_id or ""
            )
            resume_snapshot = prior.snapshot if prior is not None else None
        return {
            **({"resume_snapshot": resume_snapshot} if resume_snapshot is not None else {}),
            "model": model,
            "generation": GenerationOptions.model_validate(settings["generation"]),
            "settings_sources": binding["settings_sources"],
            "permission_preset": settings["permission"],
        }

    def submit(self, session_id, request):
        self.check_admission()
        session = self.manager.require_session(session_id)
        digest = sha256_digest(canonical_json_bytes(request.model_dump(mode="json")))
        existing = self.records.get(self.workspace_id, session_id, request.client_message_id)
        if existing is not None:
            if existing["request_digest"] != digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Client message ID has a different payload"
                )
            return self.receipt(session_id, request.client_message_id, replay=True)
        if not session_can_start_work(session.lifecycle, session.health):
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session must be active and healthy"
            )
        # An isolated Workflow node conversation is execution detail. The
        # backend owns the rejection so a stale composer cannot queue chat
        # into a log the run still owns. A Direct node shares the root
        # Session, so the root itself keeps accepting queued ordinary input.
        if not self._is_workflow_root_session(session_id) and (
            self.journal.workflows.active_runs_for_leaf_session(self.workspace_id, session_id)
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "此会话是工作流执行详情，不接受新输入；请回到发起对话操作运行",
            )
        if (
            request.intent in {"steer", "follow_up"}
            and self.active_run(session_id) != request.target_agent_run_id
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Target run is no longer active")
        continuation = chat_continuation_point(self.journal, self.workspace_id, session_id)
        if continuation is not None and request.intent != "send":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "请先继续或取消中断任务")
        if request.intent == "explicit_workflow":
            binding = self.manager.workflows.binding(session_id, request)
        else:
            binding = self._binding(session_id, request.settings)
        if continuation is not None:
            old = self.records.by_turn(self.workspace_id, continuation.safety.interrupted_turn_id)
            if old is not None:
                binding = old["binding"]
            if request.settings.model_dump(exclude_none=True):
                supplied = request.settings.model_dump(mode="json", exclude_none=True)
                if any(binding["settings"].get(k) != v for k, v in supplied.items()):
                    raise ApplicationError(ApplicationErrorCode.CONFLICT, "续跑使用原任务设置")
        if (
            request.allow_unconfined_host
            and binding["settings"]["permission"] != "full-access-manual"
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Host 授权仅适用于完整访问、手动审批模式"
            )
        if request.intent != "explicit_workflow":
            self._validate_attachments(session_id, request, binding)
        if request.attachments:
            binding["attachments"] = [a.model_dump(mode="json") for a in request.attachments]
        self.on_admit(session_id)

        def admit(_):
            if request.attachments:
                self.manager.attachments.submit(request.attachments)
            self.records.insert(
                self.workspace_id,
                session_id,
                request,
                binding,
                identity=self.application.id_source.new_id("int"),
                request_digest=digest,
                binding_digest=sha256_digest(canonical_json_bytes(binding)),
            )
            if continuation is not None:
                pause_service = self.manager.pause_service
                pause_service.resume_chat_pause(session_id, command_id=request.client_message_id)
                self.records.pause(session_id, False)
            if request.intent in {"steer", "follow_up"}:
                products = self.manager.runtimes[session_id]
                products.orchestrator.runtime_control.enqueue(
                    session_id,
                    RuntimeControlKind(request.intent),
                    request.text or "[附件输入]",
                    client_message_id=request.client_message_id,
                )

        self.journal.transact(admit)
        result = self.receipt(session_id, request.client_message_id)
        # A durable chat pause cycle ends here: new accepted input continues the
        # session (D07), so the pause fact is settled as resumed exactly once.
        pause_service = getattr(self.manager, "pause_service", None)
        if pause_service is not None and continuation is None:
            point = pause_service.chat_pause_point(session_id)
            if point is not None and point.safety is None:
                pause_service.resume_chat_pause(session_id, command_id=request.client_message_id)
        self.wake(session_id)
        self.on_change(session_id)
        return result

    def wake(self, session_id):
        if not self.records.control(session_id)["paused"]:
            self.manager.ensure_driver(
                session_id, lambda: self._drive(session_id), per_run_gate=True
            )

    async def _drive(self, session_id):
        try:
            await self._drive_unlocked(session_id)
        finally:
            # The interrupted turn has settled (or the queue drained): advance an
            # open chat pause cycle to suspended. No-op without an open cycle.
            pause_service = getattr(self.manager, "pause_service", None)
            if pause_service is not None:
                pause_service.settle_chat_pause(session_id)

    async def _drive_unlocked(self, session_id):
        while not self.records.control(session_id)["paused"]:
            pending = self.records.pending(self.workspace_id, session_id)
            if not pending:
                return
            point = self.journal.workflows.execution_pause.latest_pause_point(
                self.workspace_id, owner="chat_turn", owner_id=session_id
            )
            entry = next(
                (
                    item
                    for item in pending
                    if point is not None
                    and item["client_message_id"] == point.continuation_command_id
                ),
                pending[0],
            )
            if entry["status"] != "queued":
                self.records.pause(session_id)
                return
            key = entry["client_message_id"]
            try:
                if entry["request"]["intent"] == "explicit_workflow":
                    await self.manager.workflows.drive(entry)
                    self.records.update(self._get(session_id, key), status="settled")
                    continue
                # Runtime construction restores durable state and may classify
                # open work as interrupted. Never do that while a native
                # Workflow owns this Session's log.
                async with self.manager.execution_lock:
                    model = self.prepare_options(session_id, key)["model"]
                    products = self.manager.runtime(session_id, model)
                orchestrator = products.orchestrator
                orchestrator.execution_lock = self.manager.execution_lock

                def pre_admission(key, products=products):
                    # Native Workflow entry points can append through another
                    # Session while this cached runtime waits for the shared gate.
                    # Refresh only after acquiring that gate, and only when the
                    # durable position changed, preserving live tool context on
                    # ordinary chat turns.
                    row = self.manager.require_session(session_id)
                    records = products.session.log.snapshot().records
                    position = records[-1].sequence if records else 0
                    if position != row.conversation_position:
                        products.persistence.synchronize_projection(products.session)
                    return self.prepare_options(session_id, key)

                orchestrator.pre_admission_check = pre_admission

                def prepare(key, products=products):
                    options = self.prepare_options(session_id, key)
                    products.session.pending_full_access_grant = bool(
                        self._get(session_id, key)["request"].get("allow_unconfined_host", False)
                    )
                    return options

                orchestrator.prepare_options = prepare
                orchestrator.cancelled_is_user = lambda: session_id in self.user_stops
                orchestrator.queue_enabled = lambda: not self.records.control(session_id)["paused"]
                async for event in self.stream(
                    orchestrator, InteractionRequest.model_validate(entry["request"])
                ):
                    if isinstance(event, AgentEvent):
                        if event.type == "turn.completed":
                            consumed = self.records.by_turn(self.workspace_id, event.turn_id)
                            if consumed is not None:
                                self.records.update(consumed, status="settled")
                                self.journal.session_metadata.default_title(
                                    self.workspace_id, session_id, consumed["request"]["text"]
                                )
                        self.on_event(session_id, event)
                current = self._get(session_id, key)
                if current["agent_run_id"]:
                    terminal = self.journal.get_agent_run_terminal_metrics(
                        self.workspace_id, current["agent_run_id"]
                    )
                    reason = (
                        terminal.finish_reason.value
                        if terminal
                        else self.records.terminal_reason(current)
                    )
                    self.records.update(
                        current,
                        status="settled" if reason else "blocked",
                        reason=None if reason else "needs_recovery",
                    )
                    if reason not in {"stop", "steered"}:
                        point = self.manager.pause_service.chat_pause_point(session_id)
                        continuation_accepted = (
                            reason == "interrupted"
                            and point is not None
                            and point.fact.lifecycle == "resumed"
                            and point.safety is not None
                            and point.safety.interrupted_turn_id == current["turn_id"]
                        )
                        if not continuation_accepted:
                            self.records.pause(session_id)
                else:
                    self.records.update(current, status="blocked", reason="execution_unavailable")
                    self.records.pause(session_id)
            except ApplicationError as exc:
                self.records.update(
                    self._get(session_id, key),
                    status="blocked",
                    reason="settings_unavailable"
                    if exc.code
                    in {
                        ApplicationErrorCode.CONFLICT,
                        ApplicationErrorCode.UNAVAILABLE,
                        ApplicationErrorCode.INVALID,
                    }
                    else "execution_unavailable",
                )
                self.records.pause(session_id)
            except asyncio.CancelledError:
                self.records.pause(session_id)
                raise
            except Exception:
                self.records.update(
                    self._get(session_id, key), status="blocked", reason="execution_unavailable"
                )
                self.records.pause(session_id)
            finally:
                runtime = self.manager.runtimes.get(session_id)
                if runtime is not None:
                    # Consent belongs to this input; failed preparation must not arm recovery.
                    runtime.session.pending_full_access_grant = False
                self.user_stops.discard(session_id)
                self.on_change(session_id)

    def active_run(self, session_id):
        products = self.manager.runtimes.get(session_id)
        if products is None or not products.orchestrator.run_active:
            return None
        run_id = products.persistence.current_agent_run_id
        if (
            run_id
            and self.journal.get_agent_run_terminal_metrics(self.workspace_id, run_id) is None
        ):
            return run_id
        return None

    def _is_workflow_root_session(self, session_id):
        """True while a Workflow root TaskRun belongs to this Session."""
        return bool(
            self.journal.workflows.active_runs_for_root_session(self.workspace_id, session_id)
        )

    def queue(self, session_id):
        self.manager.require_session(session_id)
        return {
            **self.records.control(session_id),
            "active_agent_run_id": self.active_run(session_id),
            "items": [
                {
                    **self.receipt(session_id, item["client_message_id"]),
                    "text": item["request"]["text"],
                    "intent": item["request"]["intent"],
                }
                for item in self.records.pending(self.workspace_id, session_id)
            ],
        }

    def withdraw(self, session_id, key, expected_revision):
        entry = self._get(session_id, key)
        if entry["revision"] != expected_revision:
            raise ApplicationError(ApplicationErrorCode.STALE, "Interaction revision changed")
        if entry["request"]["intent"] == "explicit_workflow" and self.manager.workflows.run(entry):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Workflow 已接纳；请使用运行取消或恢复操作"
            )
        if entry["status"] not in {"queued", "blocked"} or entry["turn_id"] is not None:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Input has already been consumed")
        self.records.withdraw(entry)
        self.journal.after_commit(lambda: self.on_change(session_id))
        return self.receipt(session_id, key)

    def control(self, session_id, request):
        self.manager.require_session(session_id)
        state = self.records.control(session_id)
        if state["revision"] != request.expected_revision:
            raise ApplicationError(ApplicationErrorCode.STALE, "Control revision changed")
        if request.action == "continue_queue":
            if request.target_agent_run_id is not None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Continue queue forbids a run target"
                )
            point = chat_continuation_point(self.journal, self.workspace_id, session_id)
            if point is not None:
                self.submit(
                    session_id,
                    InteractionRequest(
                        client_message_id=request.command_id,
                        text="继续未完成的任务。",
                    ),
                )
                return {"disposition": "requested", **self.queue(session_id)}
            accepted_point = self.manager.pause_service.chat_pause_point(session_id)
            if accepted_point is not None and accepted_point.continuation_command_id:
                entry = self.records.get(
                    self.workspace_id, session_id, accepted_point.continuation_command_id
                )
                if entry is not None and entry["status"] == "blocked" and entry["turn_id"] is None:
                    self.prepare_options(session_id, entry["client_message_id"])
                    self.records.update(entry, status="queued")
            # The queue continue is also the paused chat turn's resume entry
            # (lane D resume button): settle the durable pause cycle as resumed.
            pause_service = getattr(self.manager, "pause_service", None)
            if pause_service is not None:
                pause_service.resume_chat_pause(session_id, command_id=request.command_id)
            self.records.pause(session_id, False)
            self.journal.after_commit(lambda: self.wake(session_id))
            return {"disposition": "requested", **self.queue(session_id)}
        run = self.journal.get_agent_run(self.workspace_id, request.target_agent_run_id or "")
        if run is None or run.session_id != session_id:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Target run is outside this Session"
            )
        if (
            self.journal.get_agent_run_terminal_metrics(self.workspace_id, run.agent_run_id)
            is not None
        ):
            return {"disposition": "already_terminal", **self.queue(session_id)}
        if self.active_run(session_id) != run.agent_run_id:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "Run requires recovery")
        self.records.pause(session_id)
        if request.withdraw_pending:
            for entry in self.records.pending(self.workspace_id, session_id):
                if entry["turn_id"] is None:
                    self.records.withdraw(entry)

        def stop():
            if self.active_run(session_id) == run.agent_run_id:
                self.user_stops.add(session_id)
                self.manager.drivers[session_id].cancel()
            self.on_change(session_id)

        self.journal.after_commit(stop)
        return {"disposition": "requested", **self.queue(session_id)}

    @staticmethod
    def report_wire(report):
        return {
            "report_id": report.report_id,
            "session_id": report.session_id,
            "turn_id": report.turn_id,
            "agent_run_id": report.agent_run_id,
            "status": report.status.value,
            "items": [
                {
                    "item_id": item.item_id,
                    "classification": item.classification.value,
                    "allowed_resolutions": [r.value for r in item.allowed_resolutions],
                }
                for item in report.items
            ],
        }

    def recovery_reports(self, session_id):
        self.manager.require_session(session_id)
        return {
            "reports": [
                self.report_wire(report) for report in self.manager.api.list_recovery(session_id)
            ]
        }

    def recover(self, session_id, request):
        from morrow.core.recovery import RecoveryResolution

        self.manager.require_session(session_id)
        prepared_command = None
        if request.action == "resume":
            prepared_command = self.manager.api._prepare(
                "chat_resume",
                {"session_id": session_id, "target_agent_run_id": request.target_agent_run_id},
                request.command_id,
            )
            if prepared_command[2] is not None:
                return {"disposition": "replay", "agent_run_id": prepared_command[2].result_id}
        elif request.action == "resolve":
            # Resolve(resume) may start an owner driver after its durable
            # decision commits. Prepare before the busy guard so a lost HTTP
            # response can replay the same command while that driver is still
            # settling; a retry never becomes a second resolution attempt.
            prepared_command = self.manager.api._prepare(
                "recovery_resolve",
                {
                    "report_id": request.report_id,
                    "resolution": request.resolution,
                    "item_id": request.item_id,
                },
                request.command_id,
            )
            if prepared_command[2] is not None:
                report = self.manager.api.get_recovery(prepared_command[2].result_id or "")
                if report is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                    )
                return {
                    "disposition": "replay",
                    "report": self.report_wire(report),
                }
        if session_id in self.manager.drivers or self.manager.recovery_lock.locked():
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Wait for active execution before inspecting recovery"
            )
        point = chat_continuation_point(self.journal, self.workspace_id, session_id)
        if request.action == "resume" and point is not None:
            if request.target_agent_run_id != point.safety.interrupted_agent_run_id:
                raise ApplicationError(ApplicationErrorCode.STALE, "恢复目标已变化，请刷新")
            command_id, digest, _ = prepared_command

            def accept(txn):
                self.submit(
                    session_id,
                    InteractionRequest(
                        client_message_id=command_id,
                        text="继续未完成的任务。",
                    ),
                )
                self.manager.api._receipt(
                    txn,
                    command_id=command_id,
                    operation="chat_resume",
                    digest=digest,
                    session_id=session_id,
                    result_kind="agent_run",
                    result_id=request.target_agent_run_id,
                    event_cursor=None,
                )

            self.journal.transact(accept)
            return {"disposition": "requested", "agent_run_id": request.target_agent_run_id}
        # The latest input may be a Workflow, or an unconsumed/withdrawn input
        # whose model was removed. It is not the recovery authority. The
        # restored open AgentRun supplies its frozen runtime during rehydrate.
        products = self.manager.runtime(session_id)
        orchestrator = products.orchestrator
        orchestrator.cancelled_is_user = lambda: session_id in self.user_stops
        orchestrator.queue_enabled = lambda: not self.records.control(session_id)["paused"]
        if request.action == "discover":
            products.persistence.synchronize_projection(products.session)
            return {
                **self.recovery_reports(session_id),
                "pending_resume": products.persistence.pending_resume,
                "target_agent_run_id": products.persistence.current_agent_run_id,
            }
        if request.action == "resume":
            if (
                products.persistence.open_report is not None
                or not products.persistence.pending_resume
                or products.persistence.current_agent_run_id != request.target_agent_run_id
            ):
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Inspect and resolve recovery before resuming",
                )
            command_id, digest, _ = prepared_command
            self.journal.transact(
                lambda txn: self.manager.api._receipt(
                    txn,
                    command_id=command_id,
                    operation="chat_resume",
                    digest=digest,
                    session_id=session_id,
                    result_kind="agent_run",
                    result_id=request.target_agent_run_id,
                    event_cursor=None,
                )
            )
            self.manager.ensure_driver(
                session_id, lambda: self._resume(session_id, products), per_run_gate=True
            )
            return {"disposition": "requested", "agent_run_id": request.target_agent_run_id}
        report = products.api.get_recovery(request.report_id)
        if report is None or report.session_id != session_id:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Recovery report is outside this Session"
            )
        resolution = RecoveryResolution(request.resolution)
        result = products.api.resolve_recovery(
            report,
            command_id=request.command_id,
            resolution=resolution,
            item_id=request.item_id,
            log=products.session.log,
            writer=products.persistence.writer,
            close_all=resolution is RecoveryResolution.ABORT and request.item_id is None,
        )
        self.records.pause(session_id)
        if resolution is RecoveryResolution.RESUME and result.receipt.disposition.value != "replay":
            entry = self.records.by_turn(self.workspace_id, report.turn_id)
            if entry and products.persistence.current_agent_run_id:
                self.records.rebind_run(entry, products.persistence.current_agent_run_id)
            self.manager.ensure_driver(
                session_id, lambda: self._resume(session_id, products), per_run_gate=True
            )
        self.on_change(session_id)
        return {
            "report": self.report_wire(result.value),
            "disposition": result.receipt.disposition.value,
        }

    async def _resume(self, session_id, products):
        async with self.manager.recovery_lock:
            await self._resume_unlocked(session_id, products)

    async def _resume_unlocked(self, session_id, products):
        try:
            async for event in products.orchestrator.resume_recovery():
                self.on_event(session_id, event)
                if event.type == "turn.completed":
                    entry = self.records.by_turn(self.workspace_id, event.turn_id)
                    if entry:
                        self.records.update(entry, status="settled")
        except asyncio.CancelledError:
            raise
        except Exception:
            # The original Recovery/journal evidence remains available for inspection.
            pass
        finally:
            self.user_stops.discard(session_id)
            self.records.pause(session_id)
            self.on_change(session_id)

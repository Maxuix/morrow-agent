"""Pure, purpose-specific projections from the immutable conversation log."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from morrow.adapters.state.preference_migration import legacy_entries_from_preferences
from morrow.core.compaction import (
    CompactionEntry,
    CompactionSummary,
    TokenAccounting,
    TokenAccountingBasis,
)
from morrow.core.context import ContextCheckpoint
from morrow.core.domain import canonical_json_bytes, refuse_secret_material, sha256_digest
from morrow.core.models import (
    Message,
    ModelCost,
    ModelRef,
    ModelUsage,
    Preferences,
    ProtocolModel,
    SystemMessage,
    ToolDefinition,
    ToolMessage,
    UsageAvailability,
    UserMessage,
)
from morrow.core.preferences import merge_preference_entries, merge_preferences
from morrow.runtime.conversation import ConversationSnapshot, MessageRecord, PublicTurnView
from morrow.runtime.policy import RunPolicy
from morrow.runtime.session import Session

ContextPurpose = Literal["chat", "structured"]
EstimateRequestChars = Callable[[tuple[Message, ...], tuple[ToolDefinition, ...]], int]
EstimateRequestTokens = Callable[[tuple[Message, ...], tuple[ToolDefinition, ...]], int]

_SYSTEM_BOUNDARY_PREFIX = (
    "你是 Morrow（承序），与用户协作完成当前工作空间中的任务。"
    "可用能力以本次请求列出的工具为准，权限、审批与沙箱边界由执行端实施。"
)


def render_system_boundary(tools: tuple[ToolDefinition, ...] = ()) -> str:
    """Render a truthful boundary from the frozen Provider-visible ToolSet."""
    if tools:
        provided = "本次可用工具：" + "；".join(
            f"{tool.function.name}：{tool.function.description}" for tool in tools
        )
    else:
        provided = "本次以对话方式提供帮助。"
    return _SYSTEM_BOUNDARY_PREFIX + provided


# Compatibility export for callers that need a tool-free boundary snapshot.
SYSTEM_BOUNDARY = render_system_boundary()


class ContextRequest(ProtocolModel):
    purpose: ContextPurpose
    snapshot: ConversationSnapshot
    system_messages: tuple[SystemMessage, ...]
    tools: tuple[ToolDefinition, ...]
    request_char_limit: int
    checkpoint: ContextCheckpoint | None = None


class ContextPack(ProtocolModel):
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    purpose: ContextPurpose = "chat"
    estimated_request_chars: int = 0
    cleared_cycle_count: int = 0
    dropped_turn_count: int = 0
    dropped_cycle_count: int = 0
    dropped_record_count: int = 0
    checkpoint_id: str | None = None
    estimated_context_tokens: int = 0
    accounting_basis: TokenAccountingBasis | None = None
    token_threshold: int | None = None
    compaction_required: bool = False


class ContextBudgetError(ValueError):
    code = "context_budget"


@dataclass(frozen=True, slots=True)
class CompactionCandidate:
    """A safe, non-authoritative source projection awaiting an LLM summary."""

    summary_messages: tuple[Message, ...]
    source_messages: tuple[Message, ...]
    source_start_sequence: int
    source_end_sequence: int
    first_retained_sequence: int
    tokens_before: int
    estimated_tokens_after: int
    accounting: TokenAccounting
    source_digest: str
    prompt_digest: str
    read_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    instructions: str = ""


@dataclass(frozen=True, slots=True)
class _CompactionUnit:
    """One complete turn or one complete ToolCycle eligible for compaction."""

    messages: tuple[Message, ...]
    source_start_sequence: int
    source_end_sequence: int


class ContextBuilder:
    def __init__(
        self,
        *,
        run_policy: RunPolicy,
        estimate_request_chars: EstimateRequestChars,
        estimate_request_tokens: EstimateRequestTokens | None = None,
        prompt_assembler=None,
    ) -> None:
        self.run_policy = run_policy
        self.request_char_limit = run_policy.effective_request_chars
        self.estimate_request_chars = estimate_request_chars
        self.estimate_request_tokens = estimate_request_tokens or self._pi_estimate_tokens
        self.prompt_assembler = prompt_assembler

    @property
    def max_chars(self) -> int:
        """Read-only compatibility name for Stage 1 diagnostics."""
        return self.request_char_limit

    @staticmethod
    def merge_preferences(
        global_prefs: Preferences, workspace_prefs: Preferences, session_prefs: Preferences
    ) -> Preferences:
        return merge_preferences(global_prefs, workspace_prefs, session_prefs)

    def _system_messages(
        self,
        session: Session,
        tools: tuple[ToolDefinition, ...] = (),
        checkpoint: ContextCheckpoint | None = None,
    ) -> tuple[SystemMessage, ...]:
        projection = session.run_context_projection
        skill_context = (
            projection.skill_context
            if projection is not None and projection.skill_context is not None
            else session.skill_context_projection
        )
        if projection is None and session.persisted:
            state = None
            profile = None
        elif projection is None:
            if (
                session.generic_global_preferences is not None
                or session.generic_workspace_preferences is not None
                or session.generic_session_preferences
            ):
                generic_entries = merge_preference_entries(
                    session.generic_global_preferences.entries
                    if session.generic_global_preferences is not None
                    else (),
                    session.generic_workspace_preferences.entries
                    if session.generic_workspace_preferences is not None
                    else (),
                    session.generic_session_preferences
                    + (
                        ()
                        if session.generic_global_preferences is not None
                        else legacy_entries_from_preferences(
                            "global", session.global_preferences.model_dump(mode="python")
                        )
                    )
                    + (
                        ()
                        if session.generic_workspace_preferences is not None
                        else legacy_entries_from_preferences(
                            "workspace", session.workspace_preferences.model_dump(mode="python")
                        )
                    )
                    + legacy_entries_from_preferences(
                        "session",
                        session.preferences.model_dump(mode="python"),
                        allow_session=True,
                    ),
                )
                preference_state = {
                    "entries": [entry.model_dump(mode="json") for entry in generic_entries]
                }
            else:
                effective = self.merge_preferences(
                    session.global_preferences, session.workspace_preferences, session.preferences
                )
                preference_state = effective.model_dump(exclude_none=True)
            profile = session.profile
            state = {
                "preferences": preference_state,
                "profile": profile.model_dump(exclude_none=True) if profile else None,
            }
        else:
            profile = projection.snapshot.profile
            state = {"profile": profile.model_dump(exclude_none=True) if profile else None}
            if (
                projection.snapshot.preference_projection_digest is None
                and projection.snapshot.legacy_preferences is not None
            ):
                state["legacy_preferences"] = projection.snapshot.legacy_preferences.model_dump(
                    exclude_none=True
                )
        prompt_projection = (
            projection.prompt_projection
            if projection is not None
            else getattr(session, "pending_prompt_projection", None)
        )
        if projection is not None and projection.snapshot.prompt_profile_id is not None:
            if prompt_projection is None:
                raise ContextBudgetError("冻结 Direct prompt projection 不可用")
        # A persisted projection without verified prompt bodies must never fall
        # back to re-reading live project instructions during context assembly.
        use_prompt_profile = self.prompt_assembler is not None and not (
            projection is not None and prompt_projection is None
        )
        if use_prompt_profile:
            messages = list(
                self.prompt_assembler.system_messages(
                    tools=tools,
                    projection=prompt_projection,
                )
            )
        else:
            messages = [SystemMessage(content=render_system_boundary(tools))]
        if session.compaction_summary is not None:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是此前上下文压缩形成的工作记忆，用于恢复工作状态；"
                        "请结合当前任务与工具结果核对后使用：\n"
                        + session.compaction_summary.render()
                    )
                )
            )
        if skill_context is not None and skill_context.entries:
            messages.append(SystemMessage(content=skill_context.block))
        if state is not None:
            messages.append(
                SystemMessage(
                    content="以下是用户状态上下文：\n" + json.dumps(state, ensure_ascii=False),
                )
            )
        if projection is not None and projection.preference_block:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是本次 AgentRun 冻结的用户 Preferences，"
                        "用于调整表达与协作方式：\n" + projection.preference_block
                    )
                )
            )
        if projection is not None and projection.memory_block:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是本次 AgentRun 冻结的 Project Knowledge，用作项目背景；"
                        "请结合当前文件与工具结果核对后使用：\n" + projection.memory_block
                    )
                )
            )
        if checkpoint is not None:
            messages.append(SystemMessage(content=render_checkpoint_projection(checkpoint)))
        return tuple(messages)

    def prepare_prompt_projection(
        self,
        task_text: str = "",
        *,
        target_paths=None,
    ):
        """Resolve one Direct prompt projection before durable admission."""
        if self.prompt_assembler is None:
            return None
        return self.prompt_assembler.prepare_for_task(task_text, target_paths=target_paths)

    @staticmethod
    def _chars(messages: tuple[Message, ...] | list[Message]) -> int:
        """Legacy test diagnostic; request admission uses the canonical estimator."""
        return sum(len(message.content) for message in messages if message.content is not None)

    def _pi_estimate_tokens(
        self, messages: tuple[Message, ...], tools: tuple[ToolDefinition, ...]
    ) -> int:
        """Use a stable Pi-style fallback when the Provider omits prompt usage.

        Morrow has no tokenizer dependency.  The estimator intentionally counts the canonical
        request wire in UTF-8 bytes and rounds up at four bytes per token; provider usage remains
        authoritative whenever it is available.
        """

        wire_bytes = len(
            json.dumps(
                {
                    "messages": [message.model_dump(mode="json") for message in messages],
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return max(1, math.ceil(wire_bytes / 4))

    @staticmethod
    def context_digest(
        messages: tuple[Message, ...] | list[Message],
        tools: tuple[ToolDefinition, ...] | list[ToolDefinition],
    ) -> str:
        """Identify the exact bounded projection to which provider usage belongs."""

        return sha256_digest(
            canonical_json_bytes(
                {
                    "messages": [message.model_dump(mode="json") for message in messages],
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                }
            )
        )

    def _accounting(
        self,
        session: Session,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> TokenAccounting:
        usage = getattr(session, "latest_model_usage", ModelUsage.unavailable())
        usage_digest = getattr(session, "latest_model_usage_context_digest", None)
        current_digest = self.context_digest(messages, tools)
        if (
            usage.availability is UsageAvailability.AVAILABLE
            and usage.input_tokens is not None
            and (usage_digest is None or usage_digest == current_digest)
        ):
            context_tokens = usage.input_tokens
            basis = TokenAccountingBasis.PROVIDER_USAGE
        else:
            context_tokens = self.estimate_request_tokens(messages, tools)
            basis = TokenAccountingBasis.PI_ESTIMATOR
        return TokenAccounting(
            basis=basis,
            context_tokens=context_tokens,
            context_window_tokens=self.run_policy.context_window_tokens,
            reserve_tokens=self.run_policy.reserve_tokens,
            keep_recent_tokens=self.run_policy.keep_recent_tokens,
        )

    def _request(
        self,
        session: Session,
        purpose: ContextPurpose,
        tools: tuple[ToolDefinition, ...],
        checkpoint: ContextCheckpoint | None = None,
    ) -> ContextRequest:
        checkpoint = checkpoint or session.context_checkpoint
        snapshot = session.log.snapshot()
        if checkpoint is not None:
            try:
                snapshot = project_snapshot_from_checkpoint(snapshot, checkpoint)
            except ValueError as exc:
                raise ContextBudgetError(str(exc)) from exc
        return ContextRequest(
            purpose=purpose,
            snapshot=snapshot,
            system_messages=self._system_messages(
                session, tools if purpose == "chat" else (), checkpoint
            ),
            tools=tools if purpose == "chat" else (),
            request_char_limit=self.request_char_limit,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _structured_messages(snapshot: ConversationSnapshot) -> tuple[Message, ...]:
        messages: list[Message] = []
        for turn in snapshot.public_turns():
            messages.append(turn.user.message)
            if (
                turn.terminal is not None
                and turn.terminal.finish_reason.value == "stop"
                and turn.final_assistant is not None
            ):
                messages.append(turn.final_assistant.message)
        return tuple(messages)

    @staticmethod
    def _turn_messages(turn: PublicTurnView) -> list[Message]:
        messages: list[Message] = [turn.user.message]
        for cycle in turn.cycles:
            messages.append(cycle.assistant.message)
            messages.extend(record.message for record in cycle.results)
        if turn.final_assistant is not None:
            messages.append(turn.final_assistant.message)
        return messages

    def _estimate(self, messages: list[Message] | tuple[Message, ...], tools) -> int:
        return self.estimate_request_chars(tuple(messages), tuple(tools))

    @staticmethod
    def _messages_for_boundary(
        snapshot: ConversationSnapshot, boundary: int
    ) -> tuple[Message, ...]:
        """Project complete turns after a compaction boundary.

        A current turn's User message is retained as an anchor even when an earlier completed
        cycle in that turn was compacted.  Tool calls and their results are always copied as a
        unit because the ConversationLog grammar is stricter than a generic message list.
        """

        projected: list[Message] = []
        for turn in snapshot.public_turns(require_closed=False):
            retained = [
                record.message
                for record in turn.records
                if getattr(record, "sequence", 0) >= boundary and hasattr(record, "message")
            ]
            if not retained:
                continue
            if turn.user.sequence < boundary:
                projected.append(turn.user.message)
            projected.extend(retained)
        return tuple(projected)

    @staticmethod
    def _turn_messages_from_view(turn: PublicTurnView) -> tuple[Message, ...]:
        return tuple(record.message for record in turn.records if hasattr(record, "message"))

    @staticmethod
    def _safe_file_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
        values: list[str] = []
        for group in groups:
            for value in group:
                if value not in values:
                    values.append(value)
        return tuple(values[:256])

    def prepare_compaction(
        self,
        session: Session,
        *,
        instructions: str = "",
        tools: tuple[ToolDefinition, ...] = (),
    ) -> CompactionCandidate | None:
        """Select an old complete-turn/cycle prefix for one LLM compaction request."""

        if not isinstance(instructions, str) or len(instructions) > 512:
            raise ContextBudgetError("上下文压缩指令超出安全边界")
        instructions = instructions.strip()
        try:
            refuse_secret_material(instructions, label="compaction instructions")
        except ValueError as exc:
            raise ContextBudgetError("上下文压缩指令不符合安全边界") from exc
        snapshot = session.log.snapshot()
        turns = snapshot.public_turns(require_closed=False)
        if not turns:
            return None
        boundary = session.compaction_boundary_sequence
        units: list[_CompactionUnit] = []
        for turn in turns:
            message_records = tuple(record for record in turn.records if hasattr(record, "message"))
            if not message_records:
                continue
            if turn.terminal is not None:
                groups = (message_records,)
            else:
                # An active long-running turn may contain many closed ToolCycles.  Split only at
                # cycle boundaries; the user anchor is included with the first eligible group.
                cycle_groups: list[tuple[MessageRecord, ...]] = []
                if turn.cycles:
                    cycle_groups.append((turn.user, *turn.cycles[0].records))
                    cycle_groups.extend(tuple(cycle.records) for cycle in turn.cycles[1:])
                else:
                    cycle_groups.append((turn.user,))
                if turn.final_assistant is not None:
                    if cycle_groups and cycle_groups[-1] != (turn.user,):
                        cycle_groups.append((turn.final_assistant,))
                    else:
                        cycle_groups[0] = (*cycle_groups[0], turn.final_assistant)
                groups = tuple(cycle_groups)
            for group in groups:
                eligible = tuple(record for record in group if record.sequence >= boundary)
                if not eligible:
                    continue
                # A complete closed Turn ends at its terminal record; a split active Turn ends at
                # the last ToolMessage of a closed cycle.  The persistence layer validates the
                # latter as a closed ToolCycle boundary rather than treating it as a closed Turn.
                source_end = (
                    turn.terminal.sequence
                    if turn.terminal is not None
                    else max(record.sequence for record in eligible)
                )
                units.append(
                    _CompactionUnit(
                        messages=tuple(record.message for record in eligible),
                        source_start_sequence=min(record.sequence for record in eligible),
                        source_end_sequence=source_end,
                    )
                )
        if len(units) < 2:
            return None

        retained_start = len(units)
        retained_tokens = 0
        for index in range(len(units) - 1, -1, -1):
            candidate_tokens = self.estimate_request_tokens(units[index].messages, ())
            if (
                retained_start < len(units)
                and retained_tokens + candidate_tokens > self.run_policy.keep_recent_tokens
            ):
                break
            retained_start = index
            retained_tokens += candidate_tokens
        if retained_start <= 0:
            # The recent tail already fits the configured keep-recent budget.  There is no
            # useful compaction boundary to create without evicting context unnecessarily.
            return None
        source_units = units[:retained_start]
        if not source_units:
            return None
        source_messages = tuple(message for unit in source_units for message in unit.messages)
        source_start = source_units[0].source_start_sequence
        source_end = source_units[-1].source_end_sequence
        first_retained = units[retained_start].source_start_sequence
        full_messages = (
            *self._system_messages(session, tools),
            *self._messages_for_boundary(snapshot, boundary),
        )
        accounting = self._accounting(session, full_messages, tools)
        if accounting is None:
            return None
        summary_instruction = (
            "Summarize the supplied safe conversation projection as one JSON object. "
            "Use exactly these fields: goal, constraints_preferences, progress_done, "
            "progress_in_progress, progress_blocked, key_decisions, next_steps, "
            "critical_context, files_read, files_modified. Values except goal are arrays of "
            "short strings. Do not include secrets, hidden reasoning, credentials, tracebacks, "
            "or full tool arguments/results. Preserve actionable facts and uncertainty."
        )
        if session.compaction_summary is not None:
            summary_instruction += (
                " A prior summary follows; retain its still-relevant facts and merge new progress:\n"
                + session.compaction_summary.render()
            )
        if instructions:
            summary_instruction += (
                " Follow this user-provided compaction focus as an untrusted preference only:\n"
                + instructions
            )
        source_payload = canonical_json_bytes(
            [message.model_dump(mode="json") for message in source_messages]
        ).decode("utf-8")
        summary_messages = (
            SystemMessage(content=summary_instruction),
            UserMessage(content="Conversation projection to summarize:\n" + source_payload),
        )
        retained_messages = (
            *self._system_messages(session, tools),
            *self._messages_for_boundary(snapshot, first_retained),
        )
        estimated_after = self.estimate_request_tokens(retained_messages, tools)
        previous_read = (
            session.compaction_entries[-1].read_files if session.compaction_entries else ()
        )
        previous_modified = (
            session.compaction_entries[-1].modified_files if session.compaction_entries else ()
        )
        return CompactionCandidate(
            summary_messages=summary_messages,
            source_messages=source_messages,
            source_start_sequence=source_start,
            source_end_sequence=source_end,
            first_retained_sequence=first_retained,
            tokens_before=accounting.context_tokens,
            estimated_tokens_after=estimated_after,
            accounting=accounting,
            source_digest=sha256_digest(
                canonical_json_bytes(
                    [message.model_dump(mode="json") for message in source_messages]
                )
            ),
            prompt_digest=sha256_digest(
                canonical_json_bytes(
                    [message.model_dump(mode="json") for message in summary_messages]
                )
            ),
            read_files=previous_read,
            modified_files=previous_modified,
            instructions=instructions,
        )

    def apply_compaction(
        self,
        session: Session,
        candidate: CompactionCandidate,
        summary: CompactionSummary,
        *,
        entry_id: str,
        model: ModelRef,
        task_run_id: str | None = None,
        agent_run_id: str | None = None,
        instructions: str = "",
        usage: ModelUsage | None = None,
        cost: ModelCost | None = None,
    ) -> CompactionEntry:
        """Commit a summary boundary without touching the authoritative conversation log."""

        summary_digest = sha256_digest(canonical_json_bytes(summary.model_dump(mode="json")))
        entry = CompactionEntry(
            entry_id=entry_id,
            session_id=session.session_id,
            task_run_id=task_run_id,
            agent_run_id=agent_run_id,
            model=model,
            summary=summary,
            first_retained_sequence=candidate.first_retained_sequence,
            source_start_sequence=candidate.source_start_sequence,
            source_end_sequence=candidate.source_end_sequence,
            tokens_before=candidate.tokens_before,
            estimated_tokens_after=candidate.estimated_tokens_after,
            accounting=candidate.accounting,
            prompt_digest=candidate.prompt_digest,
            source_digest=candidate.source_digest,
            summary_digest=summary_digest,
            instructions=instructions or candidate.instructions,
            read_files=self._safe_file_union(candidate.read_files, summary.files_read),
            modified_files=self._safe_file_union(candidate.modified_files, summary.files_modified),
            usage=usage or ModelUsage.unavailable(),
            cost=cost or ModelCost.unavailable(),
        )
        session.append_compaction_entry(entry)
        session.latest_model_usage = ModelUsage.unavailable()
        session.latest_model_usage_context_digest = None
        return entry

    def _chat(self, request: ContextRequest, session: Session) -> ContextPack:
        turns = list(request.snapshot.public_turns(require_closed=False))
        if not turns:
            raise ContextBudgetError("聊天上下文缺少当前用户请求")
        if any(turn.unresolved_call_ids for turn in turns):
            raise ContextBudgetError("上下文包含未闭合的工具调用")
        boundary = session.compaction_boundary_sequence
        projected = self._messages_for_boundary(request.snapshot, boundary)
        messages = (*request.system_messages, *projected)
        accounting = self._accounting(session, messages, request.tools)
        estimated = self._estimate(messages, request.tools)
        threshold = accounting.threshold_tokens
        compaction_required = (
            accounting.should_compact
            if threshold is not None
            else estimated > request.request_char_limit
        )
        self._validate_tool_pairing(messages)
        return ContextPack(
            messages=messages,
            tools=request.tools,
            purpose=request.purpose,
            estimated_request_chars=estimated,
            estimated_context_tokens=accounting.context_tokens,
            accounting_basis=accounting.basis,
            token_threshold=threshold,
            compaction_required=compaction_required,
            checkpoint_id=request.checkpoint.checkpoint_id if request.checkpoint else None,
        )

    @staticmethod
    def _validate_tool_pairing(messages: tuple[Message, ...]) -> None:
        pending: list[str] = []
        for message in messages:
            if pending:
                if not isinstance(message, ToolMessage) or message.tool_call_id != pending[0]:
                    raise ContextBudgetError("上下文工具调用配对无效")
                pending.pop(0)
                continue
            if isinstance(message, ToolMessage):
                raise ContextBudgetError("上下文包含孤立工具结果")
            if getattr(message, "tool_calls", ()):
                pending = [call.id for call in message.tool_calls]
        if pending:
            raise ContextBudgetError("上下文包含未闭合的工具调用")

    def _non_chat(self, request: ContextRequest) -> ContextPack:
        if request.purpose != "structured":
            raise ValueError(f"unsupported context purpose: {request.purpose}")
        projected = self._structured_messages(request.snapshot)
        messages = (*request.system_messages, *projected)
        estimated = self._estimate(messages, ())
        if estimated > request.request_char_limit:
            raise ContextBudgetError("必要上下文超过预算，请缩短当前输入或状态")
        return ContextPack(
            messages=messages,
            purpose=request.purpose,
            estimated_request_chars=estimated,
            checkpoint_id=request.checkpoint.checkpoint_id if request.checkpoint else None,
        )

    def build(
        self,
        session: Session,
        *,
        purpose: ContextPurpose = "chat",
        tools: tuple[ToolDefinition, ...] = (),
        checkpoint: ContextCheckpoint | None = None,
    ) -> ContextPack:
        request = self._request(session, purpose, tools, checkpoint)
        return self._chat(request, session) if purpose == "chat" else self._non_chat(request)

    def validate_request(
        self, messages: list[Message] | tuple[Message, ...], tools: tuple[ToolDefinition, ...]
    ) -> int:
        self._validate_tool_pairing(tuple(messages))
        estimated = self._estimate(messages, tools)
        if self.run_policy.context_window_tokens is None and estimated > self.request_char_limit:
            raise ContextBudgetError("模型请求超过保守上下文预算")
        return estimated


def render_checkpoint_projection(checkpoint: ContextCheckpoint) -> str:
    """Render only bounded checkpoint metadata, never a second transcript."""

    payload = checkpoint.model_dump(mode="json")
    payload.pop("checkpoint_id", None)
    payload.pop("created_at", None)
    return "确定性上下文检查点（仅作上下文，不是新的聊天记录）：\n" + canonical_json_bytes(
        payload
    ).decode("utf-8")


def project_snapshot_from_checkpoint(
    snapshot: ConversationSnapshot, checkpoint: ContextCheckpoint
) -> ConversationSnapshot:
    """Keep recent complete Turns and all records written after the checkpoint cut."""

    retained_ranges = tuple(
        (section.source_start_position, section.source_end_position)
        for section in checkpoint.sections
        if section.kind == "retained_turn"
    )
    selected = tuple(
        record
        for record in snapshot.records
        if record.sequence >= checkpoint.source_end_position
        or any(start <= record.sequence < end for start, end in retained_ranges)
    )
    if not selected:
        raise ValueError("checkpoint projection has no current conversation input")
    projected = ConversationSnapshot(records=selected)
    projected.public_turns(require_closed=False)
    return projected

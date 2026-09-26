"""Bounded, value-free projection of a headless run's durable evidence.

This is an export reader. ConversationLog and the operational journal remain the
owners of chat, request and tool records.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from morrow.core.models import AgentEvent

_MAX_BYTES = 8 * 1024 * 1024
_SAFE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_RUN_DIGEST_KEYS = frozenset(
    {
        "tool_schema_digest",
        "run_policy_digest",
        "provider_config_digest",
        "generation_digest",
        "prompt_profile_digest",
        "role_prompt_digest",
        "project_instruction_selection_digest",
    }
)


class HeadlessDiagnostics:
    def __init__(self, path: Path, session_app) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8", buffering=1)
        self._session_app = session_app
        self._key = secrets.token_bytes(32)
        self._bytes = 0
        self._truncated = False
        self._requests: dict[str, str] = {}
        self._tools: dict[str, str] = {}
        self._fingerprint_written = False
        self.agent_run_id: str | None = None

    def close(self) -> None:
        self._file.close()

    def record_error(self, exc: Exception, phase: str) -> None:
        error_class = safe_error_class(exc)
        self._write(
            "run.error",
            error_class=error_class,
            phase=phase,
            correlation_id=self.agent_run_id,
            reason_fingerprint=self._digest([error_class, phase]),
        )

    def _digest(self, value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hmac.new(self._key, encoded, hashlib.sha256).hexdigest()[:24]

    def _write(self, kind: str, **facts: object) -> None:
        if self._truncated:
            return
        line = json.dumps({"schema_version": 1, "kind": kind, **facts}, separators=(",", ":"))
        size = len(line.encode("utf-8")) + 1
        if self._bytes + size > _MAX_BYTES:
            self._truncated = True
            self._file.write('{"schema_version":1,"kind":"diagnostics.truncated"}\n')
        else:
            self._file.write(line + "\n")
            self._bytes += size
        self._file.flush()
        os.fsync(self._file.fileno())

    def record_event(self, event: AgentEvent) -> None:
        if event.type not in {"turn.started", "turn.completed", "tool.status", "status.changed"}:
            return
        if event.type == "turn.started":
            run_id = getattr(self._session_app.persistence, "current_agent_run_id", None)
            if isinstance(run_id, str):
                self.agent_run_id = run_id
        if event.type == "status.changed" and event.payload.get("status") == "model_activity":
            self._write(
                "model.activity",
                attempt_ordinal=event.payload.get("attempt_ordinal"),
                activity=event.payload.get("activity"),
                chunk_count=event.payload.get("chunk_count"),
                elapsed_seconds=event.payload.get("elapsed_seconds"),
            )
        elif event.type == "status.changed" and event.payload.get("status") == "internal_error":
            self._write(
                "run.error",
                error_class=event.payload.get("error_class"),
                phase=event.payload.get("phase"),
                correlation_id=self.agent_run_id,
                reason_fingerprint=event.payload.get("reason_fingerprint"),
            )
        elif event.type == "status.changed" and event.payload.get("status") in {
            "compacting",
            "compacted",
        }:
            self._write("context.compaction", boundary=event.payload["status"])
        elif event.type == "tool.status":
            self._write(
                "tool.status",
                call_id=self._digest(event.payload.get("call_id")),
                tool_name=event.payload.get("name"),
                status=event.payload.get("status"),
                error_code=event.payload.get("error_code"),
            )
        elif event.type == "turn.completed":
            self._write(
                "turn.completed",
                finish_reason=event.payload.get("finish_reason"),
                stop_code=event.payload.get("stop_code"),
            )
        self.snapshot()

    def snapshot(self) -> None:
        run_id = self.agent_run_id
        if not run_id:
            return
        api = self._session_app.api
        if not self._fingerprint_written:
            getter = getattr(api, "get_agent_run_fingerprint", None)
            if callable(getter):
                try:
                    fingerprint = getter(run_id)
                except Exception:
                    fingerprint = None
                if isinstance(fingerprint, dict):
                    digests = {
                        key: value
                        for key, value in fingerprint.items()
                        if key in _RUN_DIGEST_KEYS
                        and (
                            value is None
                            or isinstance(value, str)
                            and _SAFE_DIGEST.fullmatch(value)
                        )
                    }
                    self._write("run.fingerprint", agent_run_id=run_id, digests=digests)
                    self._fingerprint_written = True
        observation = api.get_agent_run_observation(run_id)
        if observation is None:
            return
        for request in observation.requests:
            usage = request.usage
            facts = {
                "request_id": request.model_request_id,
                "ordinal": request.attempt_ordinal,
                "purpose": request.purpose.value,
                "state": request.state.value,
                "admitted_at": request.admitted_at.isoformat(),
                "settled_at": request.settled_at.isoformat() if request.settled_at else None,
                "error_code": request.error_code.value if request.error_code else None,
                "usage": {
                    "availability": usage.availability.value,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                },
                "cost": {
                    "availability": request.cost.availability.value,
                    "amount_minor": request.cost.amount_minor,
                    "currency": request.cost.currency,
                },
                "compaction_required": request.compaction_required,
                "dropped_record_count": request.dropped_record_count,
            }
            signature = self._digest(facts)
            if self._requests.get(request.model_request_id) != signature:
                self._write("model.request", **facts)
                self._requests[request.model_request_id] = signature
        journal = api.journal
        for execution in journal.list_executions(api.workspace_id, agent_run_id=run_id):
            commands = execution.facts.commands if execution.facts else ()
            facts = {
                "execution_id": execution.tool_execution_id,
                "call_id": self._digest(execution.call_id),
                "tool_name": execution.tool_name,
                "state": execution.state.value,
                "disposition": execution.disposition.value,
                "created_at": execution.created_at.isoformat(),
                "executing_at": execution.executing_at.isoformat()
                if execution.executing_at
                else None,
                "closed_at": execution.closed_at.isoformat() if execution.closed_at else None,
                "argument_fingerprint": self._digest(execution.intent.arguments_digest),
                "result_fingerprint": self._digest(
                    execution.result_envelope.model_dump(mode="json")
                )
                if execution.result_envelope
                else None,
                "error_code": execution.error_code,
                "cwd": commands[-1].cwd if commands else None,
                "command_class": commands[-1].command_class if commands else None,
                "exit_code": commands[-1].exit_code if commands else None,
                "duration_ms": commands[-1].duration_ms if commands else None,
                "artifact_ids": [ref.artifact_id for ref in execution.artifact_refs],
            }
            signature = self._digest(facts)
            if self._tools.get(execution.tool_execution_id) != signature:
                self._write("tool.execution", **facts)
                self._tools[execution.tool_execution_id] = signature


def safe_error_class(exc: Exception) -> str:
    name = type(exc).__name__
    return name if _SAFE_TOKEN.fullmatch(name) else "Exception"

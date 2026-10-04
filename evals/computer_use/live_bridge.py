"""Receipt-only model transport for controlled native acceptance, without a network client.

The controller supplies every decision. This adapter never chooses a target or action.
Keep historical transports/evidence unchanged; new campaigns can use this reusable provider.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from morrow.core.image_tokens import iter_image_parts
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelEvent, ModelFinishReason


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


class ReceiptError(RuntimeError):
    """Only bounded categories leave this transport; raw exceptions never do."""


def decode_receipt(content: bytes, request_sha: str, sequence: int) -> AssistantMessage:
    if len(content) > 256 * 1024:
        raise ReceiptError("receipt_bounds")
    try:
        receipt = json.loads(content)
    except (ValueError, UnicodeError):
        raise ReceiptError("receipt_json_invalid") from None
    if not isinstance(receipt, dict) or receipt.get("request_sha256") != request_sha:
        raise ReceiptError("receipt_request_binding_failed")
    basis = receipt.get("decision_basis")
    if not isinstance(basis, str) or not basis.strip():
        raise ReceiptError("receipt_basis_missing")
    tool = receipt.get("tool")
    try:
        if tool is not None:
            if (
                not isinstance(tool, dict)
                or tool.get("name") not in {"computer_observe", "computer_action"}
                or not isinstance(tool.get("arguments"), dict)
                or receipt.get("content") is not None
            ):
                raise ReceiptError("receipt_tool_invalid")
            call = FunctionToolCall(
                id=f"luna-{sequence}",
                name=tool["name"],
                arguments=json.dumps(tool["arguments"], ensure_ascii=False, allow_nan=False),
            )
            return AssistantMessage(tool_calls=(call,))
        if not isinstance(receipt.get("content"), str):
            raise ReceiptError("receipt_content_invalid")
        return AssistantMessage(content=receipt["content"])
    except ValueError:
        raise ReceiptError("receipt_schema_invalid") from None


class LiveControllerProvider:
    def __init__(
        self,
        directory: Path,
        *,
        case: str,
        goal: str,
        delivery: str,
        timeout_seconds: float = 600,
        turn_budget: int = 18,
    ):
        self.directory = directory.resolve()
        self.case, self.goal, self.delivery = case, goal, delivery
        self.timeout_seconds, self.turn_budget = timeout_seconds, turn_budget
        self.turn = 0
        self.decisions: list[dict] = []

    async def stream(self, model, messages, tools=(), *, generation=None):
        self.turn += 1
        sequence = self.turn
        if sequence > self.turn_budget:
            raise ReceiptError("controller_turn_budget")
        directory = self.directory
        request_path = directory / f"request-{sequence:03}.json"
        receipt_path = directory / f"decision-{sequence:03}.json"
        audit_path = directory / f"receipt-{sequence:03}-status.json"
        # Never reuse an old receipt or overwrite a previous campaign's request.
        if request_path.exists() or receipt_path.exists() or audit_path.exists():
            raise ReceiptError("controller_sequence_reused")
        images = []
        for part in iter_image_parts(messages):
            content = base64.b64decode(part.data, validate=True)
            digest = sha(content)
            extension = "jpg" if part.media_type == "image/jpeg" else "png"
            path = directory / f"image-{digest}.{extension}"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(content)
            images.append(
                {"path": str(path), "sha256": digest, "width": part.width, "height": part.height}
            )
        request = {
            "protocol": "luna-live-controller-v2",
            "case": self.case,
            "sequence": sequence,
            "case_goal": self.goal,
            "frozen_delivery": self.delivery,
            "model": model.model_dump(mode="json"),
            "messages": [m.model_dump(mode="json") for m in messages],
            "tools": [
                t.model_dump(mode="json")
                for t in tools
                if t.function.name in {"computer_observe", "computer_action"}
            ],
            "images": images,
            "response_path": str(receipt_path),
            "constraints": "Use actual observations. Never retry unknown or change delivery. "
            "Only a proven stale/not_started attempt may recover with fresh refs.",
        }
        atomic_json(request_path, request)
        request_sha = sha(request_path.read_bytes())
        status = {
            "sequence": sequence,
            "request_sha256": request_sha,
            "receipt_received": False,
            "accepted": False,
            "error_category": None,
            "termination": "waiting",
        }
        atomic_json(audit_path, status)
        atomic_json(
            directory / "pending.json",
            {
                "sequence": sequence,
                "request_path": str(request_path),
                "response_path": str(receipt_path),
                "request_sha256": request_sha,
            },
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while not receipt_path.exists():
                    await asyncio.sleep(0.1)
                    yield ModelEvent(kind="activity", activity="reasoning")
            status["receipt_received"] = True
            with receipt_path.open("rb") as stream:
                content = stream.read(256 * 1024 + 1)
            status["decision_sha256"] = sha(content) if len(content) <= 256 * 1024 else None
            if sha(request_path.read_bytes()) != request_sha:
                raise ReceiptError("receipt_request_changed")
            response = decode_receipt(content, request_sha, sequence)
            status.update(accepted=True, termination="accepted")
            self.decisions.append(dict(status))
            atomic_json(audit_path, status)
            yield ModelEvent(
                kind="completed",
                message=response,
                finish_reason=ModelFinishReason.TOOL_CALLS
                if response.tool_calls
                else ModelFinishReason.STOP,
            )
        except asyncio.CancelledError:
            status.update(termination="cancelled", error_category="controller_cancelled")
            raise
        except GeneratorExit:
            status.update(termination="closed", error_category="controller_stream_closed")
            raise
        except Exception as exc:
            category = (
                str(exc)
                if isinstance(exc, ReceiptError)
                else "receipt_timeout"
                if isinstance(exc, TimeoutError)
                else "receipt_io_error"
                if isinstance(exc, OSError)
                else "controller_error"
            )
            status.update(termination="failed", error_category=category)
            raise ReceiptError(category) from None
        finally:
            # Receipt acceptance is evidence of a model decision, never an SDK entry.
            atomic_json(audit_path, status)

    async def complete(self, *args, **kwargs):
        raise ReceiptError("controller_complete_not_requested")

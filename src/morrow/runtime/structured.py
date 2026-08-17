"""Bounded structured completion used by runtime-backed application flows."""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, ValidationError

from morrow.application.context import ContextBudgetError
from morrow.core.models import UserMessage


class StructuredCompletionError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("structured result must be an object")
    return value


async def complete_structured[T: BaseModel](
    provider,
    model,
    context_builder,
    session,
    schema: type[T],
    instruction: str,
    *,
    timeout: float = 30.0,
) -> tuple[T, bool]:
    """Return a validated object and whether one repair call was needed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def complete_with_remaining_budget(messages):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        try:
            context_builder.validate_request(messages, ())
        except ContextBudgetError as exc:
            raise StructuredCompletionError(str(exc)) from exc
        return await asyncio.wait_for(provider.complete(model, messages), timeout=remaining)

    try:
        context = context_builder.build(session, purpose="structured")
    except ContextBudgetError as exc:
        raise StructuredCompletionError(str(exc)) from exc
    prompt = [*context.messages, UserMessage(content=instruction)]
    raw = await complete_with_remaining_budget(prompt)
    try:
        return schema.model_validate(extract_json(raw)), False
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as first_error:
        repair = (
            "修复上一个结构化结果。只返回一个符合要求的 JSON 对象，不要解释。\n"
            f"原始任务：{instruction}\n"
            f"目标 JSON Schema：{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"校验摘要：{type(first_error).__name__}。"
        )
        repaired = await complete_with_remaining_budget(
            [*context.messages, UserMessage(content=repair)]
        )
        try:
            return schema.model_validate(extract_json(repaired)), True
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as second_error:
            raise StructuredCompletionError(type(second_error).__name__) from None

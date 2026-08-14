"""Bounded structured completion used by runtime-backed application flows."""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, ValidationError


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
    context = context_builder.build(session, purpose=schema.__name__)
    message_type = type(context.messages[0])
    prompt = [*context.messages, message_type(role="user", content=instruction)]
    raw = await asyncio.wait_for(provider.complete(model, prompt), timeout=timeout)
    try:
        return schema.model_validate(extract_json(raw)), False
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as first_error:
        repair = (
            "上一个结果未通过 Schema 校验。只返回一个符合要求的 JSON 对象；"
            f"不要解释。校验错误类型：{type(first_error).__name__}。"
        )
        repaired = await asyncio.wait_for(
            provider.complete(
                model, [*context.messages, message_type(role="user", content=repair)]
            ),
            timeout=timeout,
        )
        try:
            return schema.model_validate(extract_json(repaired)), True
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as second_error:
            raise StructuredCompletionError(type(second_error).__name__) from None

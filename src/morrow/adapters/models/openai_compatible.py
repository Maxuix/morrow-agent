"""OpenAI-compatible streaming adapter with provider-specific field isolation."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    Message,
    ModelCost,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelProviderError,
    ModelRef,
    ModelUsage,
    ToolDefinition,
    ToolMessage,
    UsageAvailability,
    provider_error_message,
)
from morrow.core.providers import DiscoveredModel

_PROVIDER_SAFE_INTEGER = 2**53 - 1
_INTEGER_BOUND_KEYWORDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"})


def normalize_tool_schema(value):
    """Copy a tool schema into the interoperable JSON-number subset.

    Morrow's local validator deliberately accepts integers with hundreds of
    digits so it can reject them through its own bounded contract. OpenAI-
    compatible servers commonly parse schemas through IEEE-754 numbers and
    reject those otherwise-valid JSON integer bounds before inference. The
    Provider wire can safely be narrower than the local validator: a model
    cannot need a line or offset beyond JavaScript's exact integer range.
    """

    if isinstance(value, list):
        return [normalize_tool_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    for key, item in value.items():
        if key in _INTEGER_BOUND_KEYWORDS and isinstance(item, int) and not isinstance(item, bool):
            item = max(-_PROVIDER_SAFE_INTEGER, min(_PROVIDER_SAFE_INTEGER, item))
        normalized[key] = normalize_tool_schema(item)
    return normalized


def serialize_message(message: Message) -> dict:
    """Explicit field whitelist; no SDK, event, reasoning or unknown fields."""
    if isinstance(message, AssistantMessage):
        payload: dict = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        return payload
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    return {"role": message.role, "content": message.content}


def serialize_tool(tool: ToolDefinition) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.function.name,
            "description": tool.function.description,
            "parameters": normalize_tool_schema(tool.function.parameters),
        },
    }


def estimate_request_chars(
    messages: tuple[Message, ...], tools: tuple[ToolDefinition, ...] = ()
) -> int:
    """Canonical size of the Adapter-owned messages/tools request wire."""
    payload: dict = {"messages": [serialize_message(message) for message in messages]}
    if tools:
        payload["tools"] = [serialize_tool(tool) for tool in tools]
        payload["tool_choice"] = "auto"
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def estimate_message_chars(message: Message) -> int:
    return len(json.dumps(serialize_message(message), ensure_ascii=False, separators=(",", ":")))


_FINISH_REASONS: dict[str, ModelFinishReason] = {
    "stop": ModelFinishReason.STOP,
    "tool_calls": ModelFinishReason.TOOL_CALLS,
    "length": ModelFinishReason.LENGTH,
    "content_filter": ModelFinishReason.CONTENT_FILTER,
}


def _raw_usage_value(raw_usage, name: str):
    if isinstance(raw_usage, Mapping):
        return raw_usage.get(name)
    return getattr(raw_usage, name, None)


def _normalize_usage(raw_usage) -> ModelUsage:
    """Read only the stable token fields exposed by OpenAI-compatible adapters."""

    if raw_usage is None:
        return ModelUsage.unavailable()

    aliases = {
        "input_tokens": ("prompt_tokens", "input_tokens"),
        "output_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }
    values: dict[str, int] = {}
    for canonical, names in aliases.items():
        observed = [
            (name, _raw_usage_value(raw_usage, name))
            for name in names
            if _raw_usage_value(raw_usage, name) is not None
        ]
        if not observed:
            continue
        first_name, value = observed[0]
        if any(item != value for _, item in observed[1:]):
            raise ValueError(f"conflicting usage fields for {canonical}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"usage field {first_name} must be a non-negative integer")
        values[canonical] = value
    if not values:
        raise ValueError("usage payload has no supported token counts")
    if "input_tokens" in values and "output_tokens" in values and "total_tokens" not in values:
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    return ModelUsage(availability=UsageAvailability.AVAILABLE, **values)


def _merge_usage(current: ModelUsage, candidate: ModelUsage) -> ModelUsage:
    if candidate.availability is UsageAvailability.UNAVAILABLE:
        return current
    if current.availability is UsageAvailability.UNAVAILABLE:
        return candidate
    values: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        previous = getattr(current, name)
        incoming = getattr(candidate, name)
        if previous is not None and incoming is not None and previous != incoming:
            raise ValueError(f"conflicting usage values for {name}")
        values[name] = incoming if previous is None else previous
    if values["input_tokens"] is not None and values["output_tokens"] is not None:
        expected = values["input_tokens"] + values["output_tokens"]
        if values["total_tokens"] is None:
            values["total_tokens"] = expected
        elif values["total_tokens"] != expected:
            raise ValueError("usage total_tokens must equal input_tokens + output_tokens")
    return ModelUsage(availability=UsageAvailability.AVAILABLE, **values)


class _CallFragments:
    __slots__ = ("argument_parts", "call_id", "name_parts")

    def __init__(self) -> None:
        self.call_id = ""
        self.name_parts: list[str] = []
        self.argument_parts: list[str] = []


class StreamAccumulator:
    """Assembles one logical choice from OpenAI-compatible stream fragments.

    Vendor fragment indexes and raw fragments never leave this class; Runtime
    only sees the assembled AssistantMessage and the normalized finish reason.
    """

    def __init__(self) -> None:
        self.text = ""
        self.saw_text = False
        self.saw_tool_fragment = False
        self._calls: dict[int, _CallFragments] = {}
        self._finish: str | None = None

    @property
    def made_progress(self) -> bool:
        return self.saw_text or self.saw_tool_fragment

    def add_text(self, text: str) -> None:
        self.saw_text = True
        self.text += text

    def add_tool_fragment(self, fragment) -> None:
        self.saw_tool_fragment = True
        index = getattr(fragment, "index", None)
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError("tool call fragment index must be an integer")
        call_type = getattr(fragment, "type", None)
        if call_type not in (None, "", "function"):
            raise ValueError("only function tool call fragments are supported")
        call_id = getattr(fragment, "id", None)
        if call_id is not None and not isinstance(call_id, str):
            raise ValueError("tool call id must be a string")
        function = getattr(fragment, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        arguments = getattr(function, "arguments", None) if function is not None else None
        for value in (name, arguments):
            if value is not None and not isinstance(value, str):
                raise ValueError("tool call name and arguments must be strings")
        parts = self._calls.setdefault(index, _CallFragments())
        if call_id:
            if parts.call_id and parts.call_id != call_id:
                raise ValueError("conflicting tool call id for one vendor index")
            parts.call_id = call_id
        if name:
            parts.name_parts.append(name)
        if arguments:
            parts.argument_parts.append(arguments)

    def set_finish(self, finish: str) -> None:
        if not isinstance(finish, str):
            raise ValueError("finish reason must be a string")
        if self._finish is not None and self._finish != finish:
            raise ValueError("conflicting finish reasons in one stream")
        self._finish = finish

    def _build_calls(self) -> tuple[FunctionToolCall, ...]:
        calls = []
        for index in sorted(self._calls):
            parts = self._calls[index]
            name = "".join(parts.name_parts)
            if not parts.call_id or not name:
                raise ValueError("tool call is missing an id or a name")
            calls.append(
                FunctionToolCall(
                    id=parts.call_id,
                    name=name,
                    arguments="".join(parts.argument_parts),
                )
            )
        ids = [call.id for call in calls]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool call ids in one stream")
        return tuple(calls)

    def build(self) -> tuple[AssistantMessage | None, ModelFinishReason]:
        """Return the assembled message and normalized finish reason."""
        if self._finish is None:
            raise ValueError("model response is missing a normal end signal")
        reason = _FINISH_REASONS.get(self._finish)
        if reason is None:
            raise ValueError(f"unsupported finish reason: {self._finish}")
        calls = self._build_calls()
        if reason == ModelFinishReason.STOP and calls:
            reason = ModelFinishReason.TOOL_CALLS
        if reason == ModelFinishReason.TOOL_CALLS:
            if not calls:
                raise ValueError("tool_calls finish without tool call fragments")
            return AssistantMessage(content=self.text or None, tool_calls=calls), reason
        if reason == ModelFinishReason.STOP:
            if not self.text:
                return None, reason
            return AssistantMessage(content=self.text), reason
        return None, reason


def _error_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _retry_after_seconds(error: BaseException) -> float | None:
    """Read only a bounded numeric Retry-After hint from an SDK exception."""

    for item in _error_chain(error):
        headers = getattr(item, "headers", None)
        if not isinstance(headers, Mapping):
            continue
        value = headers.get("retry-after") or headers.get("Retry-After")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            return min(parsed, 60.0)
    return None


def classify_error(error: BaseException) -> ModelErrorCode:
    errors = _error_chain(error)
    for item in errors:
        if isinstance(item, ModelProviderError):
            return item.code
    if any(
        getattr(item, "status_code", None) in (401, 403) or "auth" in type(item).__name__.casefold()
        for item in errors
    ):
        return ModelErrorCode.AUTH
    if any(
        any(
            marker in str(item).casefold()
            for marker in (
                "context length",
                "context window",
                "maximum context",
                "max context",
                "too many tokens",
                "prompt is too long",
            )
        )
        or getattr(item, "status_code", None) == 413
        for item in errors
    ):
        return ModelErrorCode.CONTEXT_OVERFLOW
    if any(
        getattr(item, "status_code", None) == 429 or "rate" in type(item).__name__.casefold()
        for item in errors
    ):
        return ModelErrorCode.RATE_LIMIT
    if any(getattr(item, "status_code", None) in (400, 404, 422) for item in errors):
        return ModelErrorCode.INVALID_RESPONSE
    if any(
        isinstance(item, TimeoutError) or "timeout" in type(item).__name__.casefold()
        for item in errors
    ):
        return ModelErrorCode.TIMEOUT
    if any(
        isinstance(item, (ConnectionError, OSError))
        or any(
            marker in type(item).__name__.casefold()
            for marker in ("connect", "network", "proxy", "transport")
        )
        for item in errors
    ):
        return ModelErrorCode.NETWORK
    if any(isinstance(item, (TypeError, ValueError)) for item in errors):
        return ModelErrorCode.INVALID_RESPONSE
    return ModelErrorCode.INTERNAL


async def _close_response(response) -> None:
    for name in ("aclose", "close"):
        closer = getattr(response, name, None)
        if closer is None:
            continue
        try:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            return
        return


async def _close_client(client) -> None:
    if client is None:
        return
    closer = getattr(client, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if hasattr(result, "__await__"):
            await result
    except Exception:
        return


async def discover_openai_compatible_models(config, credential: str) -> tuple[DiscoveredModel, ...]:
    """Explicitly discover model IDs through the OpenAI-compatible models port."""

    client = None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=credential, base_url=config.base_url, timeout=60.0, max_retries=0
        )
        response = await client.models.list()
        result: list[DiscoveredModel] = []
        for item in getattr(response, "data", ()) or ():
            model_id = getattr(item, "id", None)
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            try:
                result.append(DiscoveredModel(model_id=model_id, api_model_id=model_id))
            except ValueError:
                continue
        return tuple(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = classify_error(exc)
        raise ModelProviderError(
            code,
            provider_error_message(code),
            retry_after_seconds=_retry_after_seconds(exc),
        ) from None
    finally:
        await _close_client(client)


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        api_model_ids: dict[str, str] | None = None,
        timeout: float = 60.0,
        connect_timeout: float = 20.0,
        first_token_timeout: float = 45.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.api_model_ids = api_model_ids or {}
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.first_token_timeout = first_token_timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.credential,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._client

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict]:
        return [serialize_message(message) for message in messages]

    async def stream(
        self,
        model: ModelRef,
        messages: list[Message],
        tools: tuple[ToolDefinition, ...] = (),
    ) -> AsyncIterator[ModelEvent]:
        accumulator = StreamAccumulator()
        response = None
        usage = ModelUsage.unavailable()
        completed_message: AssistantMessage | None = None
        completed_reason: ModelFinishReason | None = None
        finish_seen = False
        finish_signal: str | None = None
        try:
            request: dict = {
                "model": self.api_model_ids.get(model.model_id, model.model_id),
                "messages": [serialize_message(message) for message in messages],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                request["tools"] = [serialize_tool(tool) for tool in tools]
                request["tool_choice"] = "auto"
            try:
                async with asyncio.timeout(self.connect_timeout):
                    response = await self._get_client().chat.completions.create(**request)
            except TimeoutError:
                yield ModelEvent(
                    kind="error",
                    error_code=ModelErrorCode.NETWORK,
                    error_message=provider_error_message(ModelErrorCode.NETWORK, phase="connect"),
                )
                return
            iterator = aiter(response)
            first = True
            while True:
                try:
                    if first:
                        async with asyncio.timeout(self.first_token_timeout):
                            chunk = await anext(iterator)
                    else:
                        chunk = await anext(iterator)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    yield ModelEvent(
                        kind="error",
                        error_code=ModelErrorCode.TIMEOUT,
                        error_message=provider_error_message(
                            ModelErrorCode.TIMEOUT, phase="first_token"
                        ),
                        made_progress=accumulator.made_progress,
                        usage=usage,
                    )
                    return
                first = False
                try:
                    usage = _merge_usage(usage, _normalize_usage(getattr(chunk, "usage", None)))
                except (TypeError, ValueError):
                    usage = ModelUsage.unavailable()
                    raise
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                if len(choices) != 1:
                    raise ValueError("stream must carry exactly one logical choice")
                choice = choices[0]
                if finish_seen:
                    delta = getattr(choice, "delta", None)
                    has_semantic_delta = bool(
                        getattr(delta, "content", None)
                        or getattr(delta, "reasoning_content", None)
                        or getattr(delta, "reasoning", None)
                        or getattr(delta, "reasoning_text", None)
                        or getattr(delta, "tool_calls", None)
                    )
                    repeated_finish = getattr(choice, "finish_reason", None)
                    if has_semantic_delta or repeated_finish not in (None, finish_signal):
                        raise ValueError("semantic stream chunk appeared after finish")
                    continue
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text = getattr(delta, "content", None)
                    if text is not None and not isinstance(text, str):
                        raise ValueError("model text delta must be a string")
                    if text:
                        accumulator.add_text(text)
                        yield ModelEvent(kind="text_delta", text=text)
                    for fragment in getattr(delta, "tool_calls", None) or []:
                        accumulator.add_tool_fragment(fragment)
                finish = getattr(choice, "finish_reason", None)
                if finish is not None:
                    accumulator.set_finish(finish)
                    completed_message, completed_reason = accumulator.build()
                    finish_seen = True
                    finish_signal = finish
            if not finish_seen or completed_reason is None:
                accumulator.build()
                raise ValueError("model response is missing a normal end signal")
            yield ModelEvent(
                kind="completed",
                finish_reason=completed_reason,
                message=completed_message,
                usage=usage,
                cost=ModelCost.unavailable(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = classify_error(exc)
            yield ModelEvent(
                kind="error",
                error_code=code,
                error_message=provider_error_message(code),
                retry_after_seconds=_retry_after_seconds(exc),
                made_progress=accumulator.made_progress,
                usage=usage,
            )
        finally:
            if response is not None:
                await _close_response(response)

    async def complete(self, model: ModelRef, messages: list[Message]) -> str:
        try:
            async with asyncio.timeout(self.connect_timeout + self.first_token_timeout):
                response = await self._get_client().chat.completions.create(
                    model=self.api_model_ids.get(model.model_id, model.model_id),
                    messages=self._messages(messages),
                    stream=False,
                )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("empty model response")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message else None
            if not isinstance(content, str) or not content.strip():
                raise ValueError("model response has no visible content")
            return content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = classify_error(exc)
            raise ModelProviderError(
                code,
                provider_error_message(code),
                retry_after_seconds=_retry_after_seconds(exc),
            ) from None


def make_openai_compatible(config, credential: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config.base_url,
        credential,
        api_model_ids={model_id: value.api_model_id for model_id, value in config.models.items()},
    )

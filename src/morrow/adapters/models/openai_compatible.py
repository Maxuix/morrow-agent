"""OpenAI-compatible streaming adapter with provider-specific field isolation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    Message,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelProviderError,
    ModelRef,
    ToolDefinition,
    ToolMessage,
)


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
            "parameters": tool.function.parameters,
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


def classify_error(error: BaseException) -> ModelErrorCode:
    name = type(error).__name__.casefold()
    status = getattr(error, "status_code", None)
    if status in (401, 403) or "auth" in name:
        return ModelErrorCode.AUTH
    if status == 429 or "rate" in name:
        return ModelErrorCode.RATE_LIMIT
    if isinstance(error, TimeoutError) or "timeout" in name:
        return ModelErrorCode.TIMEOUT
    if isinstance(error, (ConnectionError, OSError)) or "connect" in name or "network" in name:
        return ModelErrorCode.NETWORK
    if isinstance(error, (TypeError, ValueError)):
        return ModelErrorCode.INVALID_RESPONSE
    return ModelErrorCode.INTERNAL


def provider_error_message(code: ModelErrorCode, *, phase: str | None = None) -> str:
    if phase == "connect":
        return "连接模型服务超时"
    if phase == "first_token":
        return "等待模型首个响应超时"
    return {
        ModelErrorCode.AUTH: "认证失败，请检查 API Key 或重新配置 Provider",
        ModelErrorCode.NETWORK: "无法连接模型服务，请检查网络后重试",
        ModelErrorCode.RATE_LIMIT: "模型服务限流，请稍后重试",
        ModelErrorCode.TIMEOUT: "等待模型响应超时",
        ModelErrorCode.INVALID_RESPONSE: "模型响应无效",
        ModelErrorCode.INTERNAL: "模型服务暂时不可用",
    }[code]


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
                api_key=self.credential, base_url=self.base_url, timeout=self.timeout
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
        try:
            request: dict = {
                "model": self.api_model_ids.get(model.model_id, model.model_id),
                "messages": [serialize_message(message) for message in messages],
                "stream": True,
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
                    )
                    return
                first = False
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                if len(choices) != 1:
                    raise ValueError("stream must carry exactly one logical choice")
                choice = choices[0]
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
                    message, reason = accumulator.build()
                    yield ModelEvent(kind="completed", finish_reason=reason, message=message)
                    return
            accumulator.build()
            raise ValueError("model response is missing a normal end signal")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = classify_error(exc)
            yield ModelEvent(
                kind="error",
                error_code=code,
                error_message=provider_error_message(code),
                made_progress=accumulator.made_progress,
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
            raise ModelProviderError(code, provider_error_message(code)) from None


def make_openai_compatible(config, credential: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config.base_url,
        credential,
        api_model_ids={model_id: value.api_model_id for model_id, value in config.models.items()},
    )

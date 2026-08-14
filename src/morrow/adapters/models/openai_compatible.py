"""OpenAI-compatible streaming adapter with provider-specific field isolation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from morrow.core.models import (
    FinishReason,
    Message,
    ModelErrorCode,
    ModelEvent,
    ModelProviderError,
    ModelRef,
)


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


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        api_model_ids: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.api_model_ids = api_model_ids or {}
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.credential, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, str]]:
        return [message.model_dump() for message in messages]

    async def stream(self, model: ModelRef, messages: list[Message]) -> AsyncIterator[ModelEvent]:
        try:
            response = await self._get_client().chat.completions.create(
                model=self.api_model_ids.get(model.model_id, model.model_id),
                messages=self._messages(messages),
                stream=True,
            )
            async for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                text = getattr(delta, "content", None) if delta else None
                if text is not None and not isinstance(text, str):
                    raise ValueError("model text delta must be a string")
                if text:
                    yield ModelEvent(kind="text_delta", text=text)
                finish = getattr(choice, "finish_reason", None)
                if finish == "stop":
                    yield ModelEvent(kind="completed", finish_reason=FinishReason.STOP)
                    return
                if finish is not None:
                    yield ModelEvent(
                        kind="error",
                        error_code=ModelErrorCode.INVALID_RESPONSE,
                        error_message="模型响应未正常结束",
                    )
                    return
            yield ModelEvent(
                kind="error",
                error_code=ModelErrorCode.INVALID_RESPONSE,
                error_message="模型响应缺少正常结束信号",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield ModelEvent(
                kind="error",
                error_code=classify_error(exc),
                error_message="模型服务暂时不可用",
            )

    async def complete(self, model: ModelRef, messages: list[Message]) -> str:
        try:
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
            raise ModelProviderError(code, "模型服务暂时不可用") from None


def make_openai_compatible(config, credential: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config.base_url,
        credential,
        api_model_ids={model_id: value.api_model_id for model_id, value in config.models.items()},
    )

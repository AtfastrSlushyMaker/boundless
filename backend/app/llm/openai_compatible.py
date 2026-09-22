import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.llm.base import ModelUnavailable


class OpenAICompatibleProvider:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key or settings.llm_api_key
        self.timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=8.0)
        self.headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key and self.api_key != "none" else {}

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=self.headers) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
                payload = response.json()
            models = [str(row.get("id", "")) for row in payload.get("data", []) if isinstance(row, dict)]
            return {"status": "connected", "provider": "openai-compatible", "model": self.model, "available_models": models[:100]}
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": "offline", "provider": "openai-compatible", "model": self.model, "detail": str(exc)[:240]}

    async def _post(self, messages: list[dict[str, str]], stream: bool = False, **options: Any) -> httpx.Response:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": options.pop("temperature", settings.llm_temperature),
            "max_tokens": options.pop("max_tokens", settings.llm_max_output_tokens),
            "stream": stream,
            **options,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, ValueError) as exc:
                raise ModelUnavailable(f"Could not reach model at {self.base_url}: {exc}") from exc

    async def complete(self, messages: list[dict[str, str]], **options: Any) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": options.pop("temperature", settings.llm_temperature),
            "max_tokens": options.pop("max_tokens", settings.llm_max_output_tokens),
            **options,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
                response.raise_for_status()
                payload = response.json()
            return str(payload["choices"][0]["message"].get("content", ""))
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise ModelUnavailable(f"Model completion failed at {self.base_url}: {exc}") from exc

    async def complete_json(self, messages: list[dict[str, str]], **options: Any) -> str:
        try:
            return await self.complete(messages, response_format={"type": "json_object"}, **options)
        except ModelUnavailable as first:
            if "400" not in str(first) and "response_format" not in str(first).casefold():
                raise
            return await self.complete(messages, **options)

    async def stream_chat(self, messages: list[dict[str, str]], **options: Any) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": options.pop("temperature", settings.llm_temperature),
            "max_tokens": options.pop("max_tokens", settings.llm_max_output_tokens),
            "stream": True,
            **options,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                            text = payload["choices"][0].get("delta", {}).get("content")
                            if text:
                                yield str(text)
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Model stream failed at {self.base_url}: {exc}") from exc

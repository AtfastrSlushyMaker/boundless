import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.llm.base import ModelUnavailable


class OllamaProvider:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or "http://127.0.0.1:11434").removesuffix("/v1").rstrip("/")
        self.model = model or settings.llm_model
        self.timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=8.0)

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
            return [str(row["name"]) for row in payload.get("models", [])
                    if isinstance(row, dict) and row.get("name")]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ModelUnavailable(f"Could not list Ollama models at {self.base_url}: {exc}") from exc

    async def health(self) -> dict[str, Any]:
        try:
            names = await self.list_models()
            return {"status": "connected", "provider": "ollama", "model": self.model,
                    "available_models": names}
        except ModelUnavailable as exc:
            return {"status": "offline", "provider": "ollama", "model": self.model,
                    "detail": str(exc)[:240]}

    async def _request(self, messages: list[dict[str, str]], stream: bool = False, json_mode: bool = False, **options: Any) -> httpx.Response:
        body = {"model": self.model, "messages": messages, "stream": stream, "think": False,
                "options": {"temperature": options.pop("temperature", settings.llm_temperature),
                            "num_predict": options.pop("max_tokens", settings.llm_max_output_tokens)}, **options}
        if json_mode:
            body["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Ollama request failed at {self.base_url}: {exc}") from exc

    async def complete(self, messages: list[dict[str, str]], **options: Any) -> str:
        response = await self._request(messages, **options)
        try:
            return str(response.json()["message"].get("content", ""))
        except (ValueError, KeyError) as exc:
            raise ModelUnavailable(f"Ollama returned an invalid response: {exc}") from exc

    async def complete_json(self, messages: list[dict[str, str]], **options: Any) -> str:
        response = await self._request(messages, json_mode=True, **options)
        try:
            return str(response.json()["message"].get("content", ""))
        except (ValueError, KeyError) as exc:
            raise ModelUnavailable(f"Ollama returned invalid JSON output: {exc}") from exc

    async def stream_chat(self, messages: list[dict[str, str]], **options: Any) -> AsyncIterator[str]:
        body = {"model": self.model, "messages": messages, "stream": True, "think": False,
                "options": {"temperature": options.pop("temperature", settings.llm_temperature),
                            "num_predict": options.pop("max_tokens", settings.llm_max_output_tokens)}, **options}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            piece = json.loads(line).get("message", {}).get("content")
                            if piece:
                                yield str(piece)
                        except (json.JSONDecodeError, AttributeError):
                            continue
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Ollama stream failed at {self.base_url}: {exc}") from exc

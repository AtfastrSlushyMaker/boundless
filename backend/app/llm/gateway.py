import asyncio
import time

import httpx

from app.core.config import settings
from app.core.secrets import get_deepseek_api_key
from app.llm.base import LLMProvider, ModelUnavailable
from app.llm.ollama import OllamaProvider
from app.llm.openai_compatible import OpenAICompatibleProvider

_ready_models: set[tuple[str, str]] = set()
_probe_tasks: dict[tuple[str, str], asyncio.Task[bool]] = {}
_retry_after: dict[tuple[str, str], float] = {}


class MLXProvider(OpenAICompatibleProvider):
    async def _probe_generation(self) -> bool:
        try:
            await self.complete([{"role": "user", "content": "Reply with OK."}], max_tokens=2, temperature=0)
            return True
        except Exception:
            return False

    async def health(self) -> dict:
        status = await super().health()
        status["provider"] = "mlx"
        if status["status"] != "connected":
            return status
        key = (self.base_url, self.model)
        task = _probe_tasks.get(key)
        if task and task.done():
            _probe_tasks.pop(key, None)
            if task.result():
                _ready_models.add(key)
            else:
                _retry_after[key] = time.monotonic() + 30
        if key in _ready_models:
            return status
        if key not in _probe_tasks and time.monotonic() >= _retry_after.get(key, 0):
            _probe_tasks[key] = asyncio.create_task(self._probe_generation())
        return {**status, "status": "loading", "detail": "Waiting for the model to load and answer a short probe."}


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        super().__init__(base_url="https://api.deepseek.com", model=model or "deepseek-flash",
                         api_key=api_key or get_deepseek_api_key() or "none")

    async def list_models(self) -> list[str]:
        if not self.headers:
            raise ModelUnavailable("Add a DeepSeek API key in Model settings to load models.")
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=self.headers) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
                payload = response.json()
            return [str(row["id"]) for row in payload.get("data", [])
                    if isinstance(row, dict) and row.get("id")]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise ModelUnavailable("DeepSeek rejected the API key (401).") from exc
            raise ModelUnavailable(f"DeepSeek model list failed (HTTP {exc.response.status_code}).") from exc
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ModelUnavailable(f"Could not load DeepSeek models: {exc}") from exc

    async def health(self) -> dict:
        try:
            models = await self.list_models()
            return {"status": "connected", "provider": "deepseek", "model": self.model, "available_models": models}
        except ModelUnavailable as exc:
            return {"status": "offline", "provider": "deepseek", "model": self.model, "detail": str(exc)[:240]}

    async def complete(self, messages: list[dict[str, str]], **options) -> str:
        return await super().complete(messages, thinking={"type": "disabled"}, **options)

    async def stream_chat(self, messages: list[dict[str, str]], **options):
        async for text in super().stream_chat(messages, thinking={"type": "disabled"}, **options):
            yield text


def get_provider(provider: str | None = None, base_url: str | None = None, model: str | None = None) -> LLMProvider:
    kind = (provider or settings.llm_provider).casefold()
    if kind == "ollama":
        return OllamaProvider(base_url=base_url or settings.llm_base_url, model=model or settings.llm_model)
    if kind == "mlx":
        return MLXProvider(base_url=base_url or settings.llm_base_url, model=model or settings.llm_model)
    if kind == "deepseek":
        return DeepSeekProvider(model=model or "deepseek-flash")
    return OpenAICompatibleProvider(base_url=base_url or settings.llm_base_url, model=model or settings.llm_model)


def provider_name() -> str:
    return settings.llm_provider

"""Pluggable text embeddings for memory retrieval.

The default is a local, deterministic hashed embedding: no network, no model download,
and nothing leaves the machine. Point EMBEDDING_PROVIDER at an Ollama or
OpenAI-compatible embedding endpoint for true semantic vectors. Rows remember which
model produced them, so switching providers never compares incompatible vectors.
"""

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import settings

TOKEN = re.compile(r"[a-z0-9']+")
STEM_SUFFIXES = ("ingly", "ings", "ing", "edly", "ed", "es", "s", "ly")


class EmbeddingProvider(Protocol):
    model: str
    local: bool

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _stem(token: str) -> str:
    for suffix in STEM_SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


class HashedEmbedding:
    """Feature-hashed bag of stems, bigrams, and character trigrams (384 dimensions)."""

    local = True

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        self.model = f"hash-{dimensions}-v1"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        stems = [_stem(token) for token in TOKEN.findall(text.casefold()) if len(token) > 1]
        features: list[tuple[str, float]] = [(f"w:{stem}", 1.0) for stem in stems]
        features += [(f"b:{left}_{right}", 0.6) for left, right in zip(stems, stems[1:], strict=False)]
        for stem in stems:
            padded = f"^{stem}$"
            features += [(f"c:{padded[index:index + 3]}", 0.25) for index in range(len(padded) - 2)]
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


class OllamaEmbedding:
    local = True

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.removesuffix("/v1").rstrip("/")
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
            response.raise_for_status()
            return [list(map(float, row)) for row in response.json()["embeddings"]]


class OpenAICompatibleEmbedding:
    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "none" else {}
        host = httpx.URL(self.base_url).host
        self.local = host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts})
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row.get("index", 0))
            return [list(map(float, row["embedding"])) for row in rows]


def get_embedding_provider() -> EmbeddingProvider | None:
    kind = (settings.embedding_provider or "hash").casefold()
    if kind == "none":
        return None
    if kind == "ollama" and settings.embedding_model:
        return OllamaEmbedding(settings.embedding_base_url or "http://127.0.0.1:11434", settings.embedding_model)
    if kind == "openai-compatible" and settings.embedding_model and settings.embedding_base_url:
        return OpenAICompatibleEmbedding(settings.embedding_base_url, settings.embedding_model, settings.llm_api_key)
    return HashedEmbedding()

"""Model roles: narrator, state tracking, summary, canon repair, mature scenes, and optional hosted fallback.

Default: every role uses the active narrator model. A role with its own saved profile
overrides that. The state fallback is used only when the user explicitly enables it,
because it may send campaign content to a hosted provider. The mature-scenes writer is
also opt-in: it never inherits, so explicit prose only moves to a model the user chose.
"""

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ModelProfile
from app.llm.base import LLMProvider
from app.llm.gateway import get_provider

ROLES = ("narrator", "state", "summary", "canon_repair", "mature", "state_fallback")
INHERITS = {"state": ("narrator",), "summary": ("state", "narrator"), "canon_repair": ("narrator",)}
FALLBACK_FLAG = "hosted_fallback_enabled"


@dataclass
class RoutedModel:
    role: str
    provider: LLMProvider
    profile: ModelProfile | None
    source_role: str

    @property
    def kind(self) -> str:
        return self.profile.provider if self.profile else settings.llm_provider

    @property
    def model_name(self) -> str:
        return self.profile.model if self.profile else settings.llm_model

    @property
    def hosted(self) -> bool:
        return is_hosted(self.kind, self.profile.base_url if self.profile else settings.llm_base_url)

    def describe(self) -> dict:
        return {"role": self.role, "source_role": self.source_role, "provider": self.kind,
                "model": self.model_name, "hosted": self.hosted}


def is_local_endpoint(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
        return host.casefold() in {"localhost", "host.docker.internal"} or ip_address(host).is_loopback \
            or ip_address(host).is_private
    except ValueError:
        return False


def is_hosted(provider: str, base_url: str) -> bool:
    if provider == "deepseek":
        return True
    if provider in {"mlx", "ollama"}:
        return False
    return not is_local_endpoint(base_url)


def provider_for_profile(profile: ModelProfile | None) -> LLMProvider:
    if not profile:
        return get_provider()
    return get_provider(profile.provider, profile.base_url, profile.model)


async def active_profile(session: AsyncSession) -> ModelProfile | None:
    return await session.scalar(select(ModelProfile).where(
        ModelProfile.active.is_(True), ModelProfile.role == "narrator").order_by(ModelProfile.updated_at.desc()))


async def role_profile(session: AsyncSession, role: str) -> ModelProfile | None:
    if role == "narrator":
        return await active_profile(session)
    return await session.scalar(select(ModelProfile).where(
        ModelProfile.role == role, ModelProfile.active.is_(True)).order_by(ModelProfile.updated_at.desc()))


async def hosted_fallback_enabled(session: AsyncSession) -> bool:
    profile = await session.scalar(select(ModelProfile).where(ModelProfile.role == "state_fallback"))
    return bool(profile and profile.active)


async def route(session: AsyncSession, role: str, provider_factory=None) -> RoutedModel:
    """Resolve a role to a provider, following the inheritance chain to the narrator."""
    factory = provider_factory or provider_for_profile
    if role == "state_fallback":
        profile = await role_profile(session, "state_fallback")
        return RoutedModel(role, factory(profile), profile, "state_fallback") if profile else None  # type: ignore[return-value]
    profile = await role_profile(session, role)
    if profile:
        return RoutedModel(role, factory(profile), profile, role)
    for parent in INHERITS.get(role, ()):
        profile = await role_profile(session, parent)
        if profile:
            return RoutedModel(role, factory(profile), profile, parent)
    return RoutedModel(role, factory(None), None, "default")


async def mature_writer(session: AsyncSession, provider_factory=None) -> RoutedModel | None:
    """The model chosen for mature scenes, or None when the user has not picked one."""
    profile = await role_profile(session, "mature")
    return RoutedModel("mature", (provider_factory or provider_for_profile)(profile), profile, "mature") if profile else None


async def visual_model(session: AsyncSession, provider_factory=None) -> RoutedModel:
    """Character appearance descriptions use the mature model when one is set, so detail is not censored."""
    return await mature_writer(session, provider_factory) or await route(session, "state", provider_factory)

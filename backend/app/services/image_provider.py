"""Provider-neutral portrait prompts, ComfyUI and AI Horde clients, and local storage."""

import json
import secrets
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.core.config import settings

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / "workflows" / "boundless_portrait_v1.json"
HORDE_BASE = "https://aihorde.net/api/v2/generate"
HORDE_HEADERS = {"apikey": "0000000000", "Client-Agent": "Boundless:0.1:local-app"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
ART_STYLES = {
    "dark_fantasy": "aged oil painting, dramatic candlelight, muted earth palette",
    "cyberpunk": "cinematic portrait, rainy neon rim light, restrained electric palette",
    "cozy": "warm storybook illustration, soft natural light",
    "horror": "atmospheric painted portrait, low-key light, muted palette",
    "sci_fi": "cinematic science fiction portrait, precise material detail",
    "romance": "elegant painterly portrait, gentle natural light",
}


def validate_endpoint(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Enter an http(s) ComfyUI server address without credentials or a path.")
    return value.strip().rstrip("/")


def importance_for(character) -> str:
    attributes = character.attributes or {}
    explicit = str(attributes.get("importance_override") or attributes.get("importance", "")).upper()
    if explicit in {"BACKGROUND", "MINOR", "RECURRING", "MAJOR", "COMPANION"}:
        return explicit
    computed = str(getattr(character, "importance", "") or "").upper()
    if computed in {"BACKGROUND", "RECURRING", "MAJOR", "COMPANION"}:
        return computed
    role = (character.role or "").lower()
    if any(word in role for word in ("companion", "party member")):
        return "COMPANION"
    if any(word in role for word in ("king", "queen", "prince", "princess", "leader", "antagonist", "priestess")):
        return "MAJOR"
    seen = (character.attributes or {}).get("seen_count", 0)
    if isinstance(seen, int) and seen >= 2:
        return "RECURRING"
    if character.name.lower().startswith(("unnamed ", "unknown ")):
        return "BACKGROUND"
    return "MINOR"


def portrait_prompt(character, campaign) -> tuple[str, str]:
    attributes = character.attributes or {}
    visual = attributes.get("visual_identity")
    appearance = attributes.get("current_appearance") or attributes.get("appearance") or ""
    parts = [character.name, character.role]
    if isinstance(visual, dict):
        for key in ("species", "apparent_age", "hair", "eyes", "skin", "build"):
            value = visual.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip()[:120])
        features = visual.get("features")
        if isinstance(features, list):
            parts.extend(str(feature)[:120] for feature in features[:5] if isinstance(feature, str))
    for key in ("species", "apparent_age", "hair", "eyes", "skin", "build"):
        value = attributes.get(key)
        if isinstance(value, str) and value.strip() and not (isinstance(visual, dict) and visual.get(key)):
            parts.append(value.strip()[:120])
    if isinstance(appearance, dict):
        parts.extend(str(value)[:150] for value in appearance.values() if isinstance(value, str))
    elif isinstance(appearance, str):
        parts.append(appearance[:400])
    faction = attributes.get("faction") or attributes.get("faction_name")
    if isinstance(faction, str):
        parts.append(f"of {faction[:100]}")
    theme = campaign.theme_profile or {}
    family = str(theme.get("family", "neutral"))
    style = ART_STYLES.get(family, "painterly fantasy portrait, natural directional light")
    accent = str(theme.get("accent_family", ""))[:60]
    positive = ", ".join(part for part in parts if part)[:950]
    positive = f"{style}, upper-body character portrait, detailed face, stable distinctive features, {positive}, {accent} accents, simple dark background, no lettering"
    negative = "text, watermark, frame, blurry face, distorted anatomy, extra limbs, nudity, sexualized pose, gore"
    return positive[:1400], negative


def stable_seed(character_id: UUID) -> int:
    return character_id.int % (2**63 - 1)


def new_seed() -> int:
    return secrets.randbits(63)


def workflow_for(profile, positive: str, negative: str, seed: int, output_tag: str = "") -> dict:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    workflow["1"]["inputs"]["ckpt_name"] = profile.checkpoint
    workflow["2"]["inputs"]["text"] = positive
    workflow["3"]["inputs"]["text"] = negative
    workflow["4"]["inputs"].update(width=profile.width, height=profile.height)
    workflow["5"]["inputs"].update(seed=seed, steps=profile.steps, cfg=profile.cfg,
                                    sampler_name=profile.sampler, scheduler=profile.scheduler)
    if output_tag:
        workflow["7"]["inputs"]["filename_prefix"] = f"BoundlessPortrait_{output_tag}"
    return workflow


class ComfyUIImageProvider:
    def __init__(self, base_url: str):
        self.base_url = validate_endpoint(base_url)

    async def health_check(self) -> dict:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            stats = response.json()
            models = await client.get(f"{self.base_url}/object_info/CheckpointLoaderSimple")
            models.raise_for_status()
            names = self._checkpoint_names(models.json())
            queue = await client.get(f"{self.base_url}/queue")
            queue.raise_for_status()
        devices = stats.get("devices", []) if isinstance(stats, dict) else []
        device = devices[0] if devices and isinstance(devices[0], dict) else {}
        queued = queue.json() if isinstance(queue.json(), dict) else {}
        return {"status": "connected", "device": str(device.get("name", "")),
                "models": names, "queue_running": len(queued.get("queue_running", [])),
                "queue_pending": len(queued.get("queue_pending", []))}

    async def get_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f"{self.base_url}/object_info/CheckpointLoaderSimple")
            response.raise_for_status()
            return self._checkpoint_names(response.json())

    @staticmethod
    def _checkpoint_names(data: object) -> list[str]:
        if not isinstance(data, dict):
            return []
        node = data.get("CheckpointLoaderSimple", {})
        if not isinstance(node, dict):
            return []
        choices = node.get("input", {}).get("required", {}).get("ckpt_name", [])
        return [str(name) for name in choices[0]] if choices and isinstance(choices[0], list) else []

    async def generate(self, workflow: dict) -> str:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/prompt", json={"prompt": workflow})
            response.raise_for_status()
            data = response.json()
        prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ValueError(f"ComfyUI rejected the workflow: {str(data)[:220]}")
        return prompt_id

    async def result(self, prompt_id: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            history = response.json()
            entry = history.get(prompt_id, {}) if isinstance(history, dict) else {}
            if not entry:
                return None
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("completed") is False:
                raise ValueError("ComfyUI reported a failed portrait workflow.")
            outputs = entry.get("outputs", {})
            images = outputs.get("7", {}).get("images", []) if isinstance(outputs, dict) else []
            if not images:
                raise ValueError("ComfyUI finished without an image output.")
            image = images[0]
            if not isinstance(image, dict) or any(not isinstance(image.get(key), str) for key in ("filename", "subfolder", "type")):
                raise ValueError("ComfyUI returned invalid image metadata.")
            async with client.stream("GET", f"{self.base_url}/view", params={key: image[key] for key in ("filename", "subfolder", "type")}) as result:
                result.raise_for_status()
                return await limited_image(result)


async def request_horde(positive: str) -> str:
    payload = {"prompt": positive, "params": {"width": 512, "height": 768, "steps": 24, "n": 1},
               "nsfw": False, "censor_nsfw": True, "allow_downgrade": True}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{HORDE_BASE}/async", json=payload, headers=HORDE_HEADERS)
        response.raise_for_status()
        job_id = response.json().get("id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("AI Horde did not return a job ID.")
    return job_id


async def horde_result(job_id: str) -> bytes | None:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        check = await client.get(f"{HORDE_BASE}/check/{job_id}", headers=HORDE_HEADERS)
        check.raise_for_status()
        state = check.json()
        if not state.get("done"):
            if state.get("faulted"):
                raise ValueError("AI Horde could not generate this portrait.")
            return None
        response = await client.get(f"{HORDE_BASE}/status/{job_id}", headers=HORDE_HEADERS)
        response.raise_for_status()
        generations = response.json().get("generations") or []
        url = generations[0].get("img") if generations else None
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == "aihorde.net" or parsed.hostname.endswith(".aihorde.net")):
            raise ValueError("AI Horde returned an unexpected image host.")
        async with client.stream("GET", url) as image:
            image.raise_for_status()
            return await limited_image(image)


async def limited_image(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("Portrait image is larger than the 12 MB limit.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or
            (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
        raise ValueError("Portrait service returned an invalid image.")
    return data


def store_portrait(data: bytes, campaign_id: UUID, character_id: UUID, portrait_id: UUID) -> str:
    if data.startswith(b"\x89PNG"):
        ext = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        ext = "webp"
    else:
        raise ValueError("Invalid portrait image.")
    root = Path(settings.portrait_storage_dir).resolve()
    relative = Path(str(campaign_id)) / str(character_id) / f"{portrait_id}.{ext}"
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid portrait path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_bytes(data)
    temporary.replace(path)
    return relative.as_posix()


def portrait_file(relative: str) -> Path:
    root = Path(settings.portrait_storage_dir).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() not in {".png", ".jpg", ".webp"}:
        raise ValueError("Invalid portrait path.")
    return candidate

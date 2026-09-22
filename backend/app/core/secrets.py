import os
import tempfile
from pathlib import Path

from app.core.config import ROOT_DIR, settings

DEEPSEEK_KEY_PATH = ROOT_DIR / ".secrets" / "deepseek_api_key"


def get_deepseek_api_key() -> str:
    if DEEPSEEK_KEY_PATH.is_file():
        return DEEPSEEK_KEY_PATH.read_text(encoding="utf-8").strip()
    return (settings.deepseek_api_key or (settings.llm_api_key if settings.llm_api_key != "none" else "")).strip()


def save_deepseek_api_key(value: str) -> None:
    key = value.strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError("Enter a valid DeepSeek API key.")
    directory: Path = DEEPSEEK_KEY_PATH.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".key-", delete=False) as temporary:
            temporary_path = temporary.name
            temporary.write(key)
            temporary.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, DEEPSEEK_KEY_PATH)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)

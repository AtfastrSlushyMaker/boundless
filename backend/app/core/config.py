from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    database_url: str = "postgresql+asyncpg://boundless:boundless_dev@127.0.0.1:54329/boundless"
    llm_provider: str = "mlx"
    llm_base_url: str = "http://127.0.0.1:8088/v1"
    llm_model: str = "lukey03/Qwen3.5-9B-abliterated-MLX-4bit"
    llm_api_key: str = "none"
    deepseek_api_key: str = ""
    llm_timeout_seconds: float = 240
    llm_context_window: int = 131072
    llm_max_output_tokens: int = 1200
    llm_temperature: float = 0.82
    embedding_model: str = ""
    host_mlx_supported: bool = False
    mlx_host_base_url: str = "http://host.docker.internal:8088/v1"

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()

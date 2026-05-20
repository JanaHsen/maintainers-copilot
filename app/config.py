from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # protected_namespaces=() lets us name the model-server host/port fields
    # with the natural `model_server_` prefix (pydantic otherwise reserves
    # the `model_` prefix for BaseModel internals).
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", protected_namespaces=()
    )

    vault_addr: str = Field(default="http://vault:8200")
    vault_dev_root_token_id: str = Field(...)

    api_port: int = Field(default=8000)
    postgres_port: int = Field(default=5432)
    redis_port: int = Field(default=6379)
    minio_port: int = Field(default=9000)
    vault_port: int = Field(default=8200)
    phoenix_port: int = Field(default=6006)

    postgres_host: str = Field(default="postgres")
    postgres_db: str = Field(default="maintainers_copilot")
    postgres_user: str = Field(default="postgres")

    redis_host: str = Field(default="redis")
    minio_host: str = Field(default="minio")
    minio_root_user: str = Field(default="minioadmin")

    model_server_host: str = Field(default="model-server")
    model_server_port: int = Field(default=8001)

    phoenix_otlp_endpoint: str = Field(default="http://phoenix:4317")


@lru_cache
def get_settings() -> Settings:
    return Settings()
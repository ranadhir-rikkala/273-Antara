from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"

    database_url: str = "postgresql://antara:antara@localhost:5433/antara"

    s3_endpoint: str = "localhost:8333"
    s3_access_key: str = "antara"
    s3_secret_key: str = "antara-secret"
    s3_bucket: str = "documents"

    llm_base_url: str = "http://localhost:8088/v1"
    llm_model: str = "qwen2.5:7b-instruct"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    llm_upstream_url: str = "http://localhost:11434/v1"
    gateway_max_inflight_chat: int = 4
    gateway_max_inflight_embed: int = 8
    gateway_wait_seconds: float = 2.0

    worker_queue: str = "ingest-cpu"
    max_concurrent_activities: int = 8
    embed_batch_size: int = 32
    gpu_offload_backlog: int = 64

    max_inflight_requests: int = 64


settings = Settings()

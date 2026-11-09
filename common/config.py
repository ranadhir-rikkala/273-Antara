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
    llm_temperature: float = 0.0
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    embedding_base_url: str = ""

    llm_upstream_url: str = "http://localhost:11434/v1"
    embed_upstream_url: str = ""
    gateway_max_inflight_chat: int = 4
    gateway_max_inflight_embed: int = 8
    gateway_wait_seconds: float = 2.0

    worker_queue: str = "ingest-cpu"
    max_concurrent_activities: int = 8
    embed_batch_size: int = 32
    gpu_offload_backlog: int = 64

    max_inflight_requests: int = 64

    gateway_url: str = "http://gateway:8080"
    gateway_pods_url: str = "http://gateway-pods:8080"
    prometheus_url: str = "http://prometheus:9090"
    jaeger_query_url: str = "http://jaeger:16686/jaeger"
    model_catalog_url: str = ""
    pinned_model_digests: dict[str, str] = {}
    k8s_namespace: str = "antara"
    chaos_enabled: bool = False
    ops_target_url: str = "http://api:8000"
    reconcile_every_seconds: int = 120
    reconcile_stale_minutes: int = 5

    auth_mode: str = "headers"
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = "antara-web"

    service_name: str = "antara"
    otel_endpoint: str = ""
    temporal_metrics_port: int = 0

    incident_similarity: float = 0.82
    incident_window_minutes: int = 60
    access_read_seconds: int = 30 * 86400
    access_write_seconds: int = 7 * 86400

    cache_similarity: float = 0.95
    cache_ttl_hours: int = 24
    cache_min_citation_score: float = 0.6
    answer_min_score: float = 0.6
    max_ingest_backlog: int = 500
    max_agent_backlog: int = 100
    max_documents_in_flight: int = 1000


settings = Settings()

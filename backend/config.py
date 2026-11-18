from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Groq LLM
    groq_api_key: str = "YOUR_GROQ_API_KEY_HERE"
    groq_triage_model: str = "llama-3.1-8b-instant"
    groq_main_model: str = "llama-3.3-70b-versatile"
    groq_verify_model: str = "llama-3.1-8b-instant"

    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_cache_ttl: int = 300
    redis_ttl_jitter: int = 45

    # Database
    database_url: str = "sqlite+aiosqlite:///./unreliable_assistant.db"

    # RAG hyperparameters
    rerank_threshold: float = 0.42
    cache_cosine_threshold: float = 0.92
    bm25_k1: float = 1.25
    bm25_b: float = 0.75
    bm25_top_k: int = 50
    dense_top_k: int = 50
    rrf_k: int = 60
    rerank_top_k: int = 4

    # Control plane
    max_react_iterations: int = 5
    per_step_timeout_ms: int = 120
    total_loop_budget_ms: int = 450

    # RAGAS gates
    ragas_faithfulness_min: float = 0.92
    ragas_relevancy_min: float = 0.90
    ragas_entity_recall_min: float = 0.85

    # Observability
    otel_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "unreliable-assistant"

    # App
    app_env: str = "development"
    log_level: str = "INFO"


settings = Settings()

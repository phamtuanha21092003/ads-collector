from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_uri: str = ""
    db_name: str = "ads_collector"
    rabbitmq_uri: str = ""
    rabbitmq_queue: str = "ads_collector.jobs"
    rabbitmq_prefetch: int = 1
    rabbitmq_dlx: str = "ads_collector.jobs.dlx"
    rabbitmq_dlq: str = "ads_collector.jobs.dlq"
    rabbitmq_dlq_routing_key: str = "ads_collector.jobs.dlq"
    rabbitmq_max_retries: int = 3
    rabbitmq_retry_delay_seconds: float = 5.0
    worker_id: str = "worker-1"
    default_country: str = "US"
    max_results_cap: int = 100000
    trendtrack_api_key:str=''
    trendtrack_api_url:str=''


@lru_cache
def get_settings() -> Settings:
    return Settings()

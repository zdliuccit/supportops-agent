"""SupportOps 服务端配置及生产环境安全约束。"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_SECRET = "supportops-development-secret-change-me"
DEFAULT_SECRET_ENCRYPTION_KEY = "supportops-local-model-secret-key-change-me"
DEFAULT_BOOTSTRAP_ADMIN_PASSWORD = "ZDLIU@246810jia"


class Settings(BaseSettings):
    """从 ``SUPPORTOPS_`` 环境变量和本地 ``.env`` 加载运行配置。"""

    model_config = SettingsConfigDict(
        env_prefix="SUPPORTOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "dev", "staging", "production", "test"] = "local"
    service_name: str = "supportops-api"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://agent:agent_dev@localhost:5432/supportops"
    redis_url: str = "redis://localhost:6379/0"
    redis_queue_name: str = "supportops:runs"
    jwt_secret: str = Field(default=DEFAULT_DEVELOPMENT_SECRET, min_length=32)
    jwt_issuer: str = "supportops-local"
    jwt_audience: str = "supportops-api"
    jwt_algorithm: Literal["HS256"] = "HS256"
    bootstrap_admin_enabled: bool = True
    bootstrap_company_name: str = "SupportOps"
    bootstrap_company_slug: str = "supportops"
    bootstrap_admin_email: str = "zdliuccit@gmail.com"
    bootstrap_admin_password: str = DEFAULT_BOOTSTRAP_ADMIN_PASSWORD
    bootstrap_admin_name: str = "系统管理员"
    access_token_lifetime_minutes: int = Field(default=480, ge=5, le=43_200)
    cors_origins: list[str] = ["http://localhost:5173"]
    sse_poll_interval_seconds: float = Field(default=0.25, gt=0, le=5)
    sse_heartbeat_seconds: float = Field(default=10, gt=0, le=60)
    worker_recovery_interval_seconds: float = Field(default=30, gt=0, le=300)
    langgraph_checkpoint_retention_days: int = Field(default=30, ge=1, le=3650)
    langgraph_checkpoint_cleanup_interval_seconds: float = Field(
        default=3600, gt=60, le=86_400
    )
    secret_encryption_key: str = Field(default=DEFAULT_SECRET_ENCRYPTION_KEY, min_length=32)
    model_endpoint_allowed_hosts: list[str] = Field(default_factory=list)
    model_endpoint_allow_private_networks: bool = False
    model_test_timeout_seconds: float = Field(default=15, gt=0, le=60)
    model_test_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    agent_model_call_limit_max: int = Field(default=12, ge=1, le=100)
    agent_tool_call_limit_max: int = Field(default=20, ge=1, le=200)
    agent_run_timeout_seconds_max: int = Field(default=600, ge=10, le=3600)
    agent_max_parallel_tools_max: int = Field(default=8, ge=1, le=64)
    langgraph_checkpoint_schema: str = Field(
        default="supportops_checkpoints",
        pattern=r"^[a-z][a-z0-9_]{0,62}$",
    )

    @model_validator(mode="after")
    def protect_production(self) -> "Settings":
        """拒绝在生产环境启用开发默认值或高风险网络策略。"""

        if self.environment == "production":
            if self.jwt_secret == DEFAULT_DEVELOPMENT_SECRET:
                raise ValueError("production 环境禁止使用默认 JWT 密钥")
            if self.bootstrap_admin_password == DEFAULT_BOOTSTRAP_ADMIN_PASSWORD:
                raise ValueError("production 环境禁止使用默认管理员示例密码")
            if self.secret_encryption_key == DEFAULT_SECRET_ENCRYPTION_KEY:
                raise ValueError("production 环境禁止使用默认模型密钥加密主密钥")
            if self.model_endpoint_allow_private_networks:
                raise ValueError("production 环境禁止允许模型端点访问私网")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("production 环境必须使用 PostgreSQL 与持久化 checkpointer")
            if not self.model_endpoint_allowed_hosts:
                raise ValueError("production 环境必须配置模型端点域名允许列表")
        return self


@lru_cache
def get_settings() -> Settings:
    """返回进程级缓存配置，避免每次依赖注入重复读取环境。"""

    return Settings()

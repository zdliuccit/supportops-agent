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
    # 本地记录模型流的开始、输出中和结束；其他环境需要显式开启。
    llm_stream_log_enabled: bool | None = None
    database_url: str = "postgresql+asyncpg://agent:agent_dev@localhost:5432/supportops"
    redis_url: str = "redis://localhost:6379/0"
    redis_queue_name: str = Field(default="supportops:runs", min_length=1, max_length=200)
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
    sse_poll_interval_seconds: float = Field(default=0.1, gt=0, le=5)
    sse_heartbeat_seconds: float = Field(default=10, gt=0, le=60)
    worker_recovery_interval_seconds: float = Field(default=30, gt=0, le=300)
    worker_heartbeat_interval_seconds: float = Field(default=5, gt=0, le=60)
    worker_service_lease_seconds: float = Field(default=15, gt=0, le=300)
    worker_health_refresh_interval_seconds: float = Field(default=30, gt=0, le=300)
    worker_maintenance_timeout_seconds: float = Field(default=60, gt=0, le=300)
    worker_max_concurrency: int = Field(default=1, ge=1, le=32)
    worker_scheduler_policy: Literal["tenant_round_robin", "fifo"] = "tenant_round_robin"
    model_probe_poll_seconds: float = Field(default=2, gt=0, le=30)
    model_probe_lease_seconds: float = Field(default=60, ge=15, le=600)
    model_probe_max_attempts: int = Field(default=3, ge=1, le=10)
    model_probe_max_age_seconds: float = Field(default=600, ge=30, le=3600)
    model_probe_run_timeout_seconds: float = Field(default=60, gt=0, le=300)
    worker_shutdown_grace_seconds: float = Field(default=30, ge=0, le=600)
    worker_shutdown_cancel_seconds: float = Field(default=15, gt=0, le=120)
    worker_heartbeat_failure_limit: int = Field(default=3, ge=1, le=10)
    run_outbox_interval_seconds: float = Field(default=2, gt=0, le=60)
    run_outbox_batch_size: int = Field(default=20, ge=1, le=100)
    run_outbox_enqueue_timeout_seconds: float = Field(default=2, gt=0, le=30)
    run_outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    run_outbox_max_age_seconds: float = Field(default=86400, gt=0, le=604800)
    run_outbox_retry_base_seconds: float = Field(default=2, gt=0, le=60)
    run_outbox_retry_max_seconds: float = Field(default=300, gt=0, le=3600)
    run_outbox_redelivery_seconds: float = Field(default=30, gt=0, le=600)
    run_attempt_lease_seconds: float = Field(default=60, ge=10, le=600)
    run_attempt_heartbeat_seconds: float = Field(default=10, gt=0, le=60)
    run_attempt_recovery_interval_seconds: float = Field(default=5, gt=0, le=300)
    run_attempt_max_recoveries: int = Field(default=2, ge=0, le=10)
    tenant_run_limit_per_day: int = Field(default=1000, ge=1, le=1_000_000)
    tenant_tool_limit_per_day: int = Field(default=10_000, ge=1, le=10_000_000)
    tenant_max_active_runs: int = Field(default=16, ge=1, le=1000)
    tool_argument_max_bytes: int = Field(default=10_000, ge=1024, le=1_000_000)
    tool_result_max_bytes: int = Field(default=20_000, ge=1024, le=1_000_000)
    knowledge_index_interval_seconds: float = Field(default=5, gt=0, le=300)
    knowledge_index_batch_size: int = Field(default=10, ge=1, le=100)
    knowledge_index_max_attempts: int = Field(default=6, ge=1, le=20)
    knowledge_search_timeout_seconds: float = Field(default=5, gt=0, le=30)
    agent_default_max_input_tokens: int = Field(default=32768, ge=1024, le=2_000_000)
    agent_context_history_turns: int = Field(default=30, ge=1, le=1000)
    tenant_model_call_limit_per_day: int = Field(default=10_000, ge=1, le=10_000_000)
    tenant_model_cost_usd_per_day: float | None = Field(default=None, gt=0, le=1_000_000)
    diagnostic_mock_enabled: bool = False
    business_action_mock_enabled: bool = False
    business_action_mock_auto_create_enabled: bool = False
    approval_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    action_poll_seconds: float = Field(default=2, gt=0, le=30)
    action_lease_seconds: float = Field(default=60, ge=15, le=600)
    action_target_timeout_seconds: float = Field(default=5, gt=0, le=30)
    action_max_executions: int = Field(default=2, ge=1, le=3)
    action_max_reconciliations: int = Field(default=6, ge=1, le=10)
    action_max_manual_reconciliations: int = Field(default=10, ge=1, le=20)
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
    def validate_worker_lease(self) -> "Settings":
        """为心跳重试预留租约空间，避免配置必然造成误判离线。"""
        if self.worker_service_lease_seconds <= 2 * self.worker_heartbeat_interval_seconds:
            raise ValueError("Worker 服务租约必须大于两倍心跳周期")
        if self.run_attempt_lease_seconds <= 2 * self.run_attempt_heartbeat_seconds:
            raise ValueError("Run 执行租约必须大于两倍续租周期")
        if self.action_lease_seconds <= 2 * self.action_target_timeout_seconds:
            raise ValueError("动作租约必须大于两倍目标调用超时")
        return self

    @model_validator(mode="after")
    def protect_production(self) -> "Settings":
        """拒绝在生产环境启用开发默认值或高风险网络策略。"""

        if self.business_action_mock_auto_create_enabled and not self.business_action_mock_enabled:
            raise ValueError("自动 Mock 建单必须同时显式启用隔离动作 Mock")
        if self.environment == "production":
            if self.diagnostic_mock_enabled or self.business_action_mock_enabled:
                raise ValueError("production 环境禁止启用隔离业务诊断或动作 Mock")
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

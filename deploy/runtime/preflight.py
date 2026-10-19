"""显式只读部署预检；不迁移、不 bootstrap、不调用模型、不解封业务凭据。"""
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from supportops_core.config import get_settings
from supportops_core.model_config import resolve_and_validate_endpoint, validate_endpoint_url_policy
from supportops_core.models import Base, ModelEndpoint, ModelEndpointVersion
from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.restore_startup_barrier import RestoreStartupBlocked, assert_service_start_allowed
from supportops_core.schema_structure import (
    MIGRATION_LOCK_KEY,
    SchemaStructureError,
    require_public_read_only_schema,
    verify_business_schema,
)
from supportops_core.secrets import create_secret_provider


class PreflightBlocked(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def explicit_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or any(marker in value.lower() for marker in ("<", ">", "replace-with", "change-me")):
        raise PreflightBlocked("CONFIG_MISSING_OR_PLACEHOLDER")
    return value


async def preflight() -> dict[str, object]:
    expected = explicit_value("SUPPORTOPS_EXPECTED_ENVIRONMENT")
    if expected not in {"staging", "production"}:
        raise PreflightBlocked("ENVIRONMENT_NOT_APPROVED")
    for name in ("SUPPORTOPS_DATABASE_URL", "SUPPORTOPS_REDIS_URL", "SUPPORTOPS_JWT_SECRET",
                 "SUPPORTOPS_SECRET_ENCRYPTION_KEY", "SUPPORTOPS_BOOTSTRAP_ADMIN_PASSWORD"):
        explicit_value(name)
    settings = get_settings()
    try:
        assert_service_start_allowed(mode=settings.restore_mode, expected_restore_id=settings.restore_operation_id,
            expected_context_digest=settings.restore_context_digest)
    except RestoreStartupBlocked as exc:
        raise PreflightBlocked(exc.code) from None
    if settings.environment != expected or settings.bootstrap_admin_enabled:
        raise PreflightBlocked("ENVIRONMENT_OR_BOOTSTRAP_MISMATCH")
    if settings.diagnostic_mock_enabled or settings.business_action_mock_enabled:
        raise PreflightBlocked("MOCK_ENABLED_IN_DELIVERY_TEMPLATE")
    database = make_url(settings.database_url)
    queue = urlsplit(settings.redis_url)
    if database.drivername != "postgresql+asyncpg" or not database.host or queue.scheme not in {"redis", "rediss"} or not queue.hostname:
        raise PreflightBlocked("DEPENDENCY_URL_INVALID")
    if not settings.model_endpoint_allowed_hosts or any("<" in host or ">" in host for host in settings.model_endpoint_allowed_hosts):
        raise PreflightBlocked("MODEL_ALLOWLIST_MISSING")
    # 仅核对当前 provider 能构造，不声称企业 provider/每条历史凭据已验证。
    create_secret_provider(settings)
    checks: dict[str, object] = {"configuration": "passed", "secret_provider_wiring": "passed"}
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        async with engine.connect() as connection:
            # 取得共享锁后再读取新的READ COMMITTED快照，避免使用取锁前的旧迁移快照。
            await connection.execution_options(isolation_level="READ COMMITTED")
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            await connection.execute(text("SET LOCAL lock_timeout = '1s'"))
            # 同迁移独占锁互斥；事务结束自动释放，不等待、不迁移或修改业务数据。
            locked = await connection.scalar(
                text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            if locked is not True:
                raise PreflightBlocked("SCHEMA_MAINTENANCE_BUSY")

            def schema(sync_connection: Connection) -> dict[str, object]:
                try:
                    require_public_read_only_schema(sync_connection)
                except SchemaStructureError as error:
                    raise PreflightBlocked(error.code) from None
                code_heads = tuple(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
                context = MigrationContext.configure(sync_connection)
                current = tuple(context.get_current_heads())
                if not code_heads or set(current) != set(code_heads):
                    raise PreflightBlocked("SCHEMA_REVISION_MISMATCH")
                inspector = inspect(sync_connection)
                try:
                    structure = verify_business_schema(
                        sync_connection,
                        metadata=Base.metadata,
                        compare_types=context.impl.compare_type,
                    )
                    reference_history = verify_reference_history_guards(sync_connection)
                except SchemaStructureError as error:
                    raise PreflightBlocked(error.code) from None
                checkpoint_tables = set(inspector.get_table_names(schema=settings.langgraph_checkpoint_schema))
                if not {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= checkpoint_tables:
                    raise PreflightBlocked("CHECKPOINT_SCHEMA_MISSING")
                return {
                    "status": "passed",
                    "revisions": list(current),
                    "business_tables": len(Base.metadata.tables),
                    "structure": structure,
                    "reference_history": reference_history,
                    "checkpoint_structure_verified": False,
                    "maintenance_lock": "shared_transaction",
                    "transaction_isolation": "read_committed",
                    "statement_timeout_ms": 5000,
                    "lock_timeout_ms": 1000,
                }

            checks["schema"] = await connection.run_sync(schema)
            # 只查询地址元数据；固定历史版本也不能绕过新的允许列表。
            urls = (await connection.scalars(select(ModelEndpointVersion.base_url)
                .join(ModelEndpoint, (ModelEndpoint.id == ModelEndpointVersion.endpoint_id)
                    & (ModelEndpoint.tenant_id == ModelEndpointVersion.tenant_id))
                .where(ModelEndpoint.is_enabled.is_(True), ModelEndpoint.deleted_at.is_(None), ModelEndpointVersion.deleted_at.is_(None))
                .distinct().order_by(ModelEndpointVersion.base_url).limit(1001))).all()
            if len(urls) > 1000:
                raise PreflightBlocked("MODEL_TARGET_SCAN_LIMIT")
            for url in urls:
                normalized = validate_endpoint_url_policy(url, allow_private_networks=settings.model_endpoint_allow_private_networks,
                    allowed_hosts=settings.model_endpoint_allowed_hosts)
                await validate_dns(normalized, settings.model_endpoint_allow_private_networks)
            checks["model_target_policy"] = {"status": "passed", "checked_addresses": len(urls), "model_requests": 0}
            await connection.rollback()
        if not await redis.ping():
            raise PreflightBlocked("REDIS_UNAVAILABLE")
        checks["redis"] = "passed"
    finally:
        await redis.aclose()
        await engine.dispose()
    return {"status": "passed", "environment": expected, "checks": checks, "smoke_executed": False}


async def validate_dns(url: str, allow_private: bool) -> None:
    # 系统 getaddrinfo 在线程中不能协作取消；用可终止子进程防止超时后等待解析器退出。
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        str(Path(__file__).absolute()),
        "--dns-policy",
        env={},
        close_fds=True,
        cwd="/",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(3):
            await process.communicate(json.dumps({"url": url, "allow_private": allow_private}).encode())
        if process.returncode != 0:
            raise PreflightBlocked("MODEL_TARGET_DNS_POLICY_INVALID")
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def bounded_preflight() -> dict[str, object]:
    async with asyncio.timeout(60):
        return await preflight()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # 帮助或拼错参数不得加载Settings、连接依赖；错误也不回显用户传入的值。
    if arguments in (["-h"], ["--help"]):
        print(
            "用法：python deploy/runtime/preflight.py\n"
            "仅在显式配置的获授权环境运行只读部署预检；不执行迁移或隔离smoke。"
        )
        return 0
    if arguments == ["--dns-policy"]:
        try:
            payload = json.loads(sys.stdin.buffer.read(16384))
            if (
                not isinstance(payload.get("url"), str)
                or not isinstance(payload.get("allow_private"), bool)
            ):
                raise ValueError("invalid DNS policy input")
            asyncio.run(resolve_and_validate_endpoint(
                payload["url"], allow_private_networks=payload["allow_private"]
            ))
        except Exception:
            return 1
        except KeyboardInterrupt:
            return 130
        return 0
    if arguments:
        print(
            json.dumps({
                "status": "blocked", "code": "PREFLIGHT_ARGUMENTS_INVALID", "smoke_executed": False,
            }),
            file=sys.stderr,
        )
        return 2
    try:
        print(json.dumps(asyncio.run(bounded_preflight()), ensure_ascii=False))
    except KeyboardInterrupt:
        print(
            json.dumps({
                "status": "blocked", "code": "PREFLIGHT_INTERRUPTED", "smoke_executed": False,
            }),
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        if isinstance(exc, PreflightBlocked):
            code = exc.code
        elif isinstance(exc, TimeoutError):
            code = "PREFLIGHT_TIMEOUT"
        else:
            code = "PREFLIGHT_DEPENDENCY_OR_CONFIG_ERROR"
        # 禁止输出原异常、连接串、目标地址或 Pydantic 的原始输入。
        print(json.dumps({"status": "blocked", "code": code, "smoke_executed": False}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

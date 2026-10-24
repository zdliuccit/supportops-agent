"""运行真实 Chromium → API → 空库迁移 PostgreSQL 的知识治理验收。

入口：uv run --all-packages python tests/run_knowledge_browser.py
只创建随机名称/随机端口的临时资源，结束后删除本轮容器。
应用工厂不使用本地 .env；已有迁移入口由显式隔离数据库 URL 覆盖连接配置。
"""

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_browser_app():
    """仅供本脚本启动的 Uvicorn 工厂，不启用 bootstrap 或外部连接器。"""
    from supportops_api.main import create_app
    from supportops_core.config import Settings

    return create_app(Settings(_env_file=None))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def seed(database_url, password):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from supportops_core.enums import KnowledgeSourceEnvironment
    from supportops_core.knowledge_services import create_knowledge_source
    from supportops_core.models import OrganizationUnit, Tenant, User
    from supportops_core.passwords import hash_password

    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            tenant = Tenant(name="隔离浏览器验收", slug="knowledge-browser-test")
            session.add(tenant)
            await session.flush()
            unit = OrganizationUnit(tenant_id=tenant.id, name="浏览器验收部门")
            session.add(unit)
            await session.flush()
            users = []
            for name, role in [
                ("author", "platform_admin"),
                ("reviewer", "platform_admin"),
                ("reader", "employee"),
            ]:
                user = User(
                    tenant_id=tenant.id,
                    email=f"{name}@knowledge.example.test",
                    display_name=f"验收{name}",
                    password_hash=hash_password(password),
                    roles=[role],
                    organization_unit_id=unit.id,
                )
                session.add(user)
                users.append(user)
            await session.flush()
            source = await create_knowledge_source(
                session,
                tenant_id=tenant.id,
                actor_user_id=users[0].id,
                external_key="browser-manual",
                name="浏览器验收来源",
                source_type="manual",
                environment=KnowledgeSourceEnvironment.DEV,
                owner_user_id=users[0].id,
                correlation_id="browser-seed",
            )
            return {
                "source_id": str(source.id),
                "author_id": str(users[0].id),
                "reviewer_id": str(users[1].id),
                "reader_id": str(users[2].id),
                "department_id": str(unit.id),
            }
    finally:
        await engine.dispose()


def main():
    if not shutil.which("docker") or not shutil.which("corepack"):
        raise SystemExit("需要 Docker 和项目已有的 Corepack/pnpm 环境")
    name = "supportops-knowledge-browser-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    pg_password = secrets.token_hex(24)
    api_port, web_port = free_port(), free_port()
    api = None
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-p",
                "127.0.0.1::5432",
                "-e",
                "POSTGRES_DB=supportops_knowledge_browser",
                "-e",
                "POSTGRES_PASSWORD",
                "pgvector/pgvector:pg17",
            ],
            env={**os.environ, "POSTGRES_PASSWORD": pg_password},
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for _ in range(100):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True
                ).returncode
                == 0
            ):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("隔离 PostgreSQL 未就绪")
        details = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
        pg_port = details["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        database_url = f"postgresql+asyncpg://postgres:{pg_password}@127.0.0.1:{pg_port}/supportops_knowledge_browser"
        env = {
            **os.environ,
            "SUPPORTOPS_ENVIRONMENT": "test",
            "SUPPORTOPS_DATABASE_URL": database_url,
            "SUPPORTOPS_REDIS_URL": "redis://127.0.0.1:1/0",
            "SUPPORTOPS_BOOTSTRAP_ADMIN_ENABLED": "false",
            "SUPPORTOPS_JWT_SECRET": secrets.token_hex(32),
            "SUPPORTOPS_LOG_LEVEL": "WARNING",
            "SUPPORTOPS_CORS_ORIGINS": json.dumps([f"http://127.0.0.1:{web_port}"]),
            "SUPPORTOPS_KNOWLEDGE_E2E_API_URL": f"http://127.0.0.1:{api_port}",
            "SUPPORTOPS_KNOWLEDGE_E2E_WEB_PORT": str(web_port),
            "SUPPORTOPS_KNOWLEDGE_E2E_PASSWORD": password,
        }
        migration = subprocess.run(
            ["uv", "run", "--all-packages", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if migration.returncode:
            print(migration.stderr.replace(pg_password, "[test-secret]"))
            raise RuntimeError("隔离迁移失败")
        env["SUPPORTOPS_KNOWLEDGE_E2E_FIXTURE"] = json.dumps(
            asyncio.run(seed(database_url, password))
        )
        with tempfile.TemporaryDirectory(prefix="supportops-knowledge-browser-") as temporary:
            env["SUPPORTOPS_KNOWLEDGE_E2E_OUTPUT_DIR"] = str(Path(temporary) / "playwright-results")
            # 本机全局 pnpm 可能不是项目版本，子进程通过 Corepack shim 使用锁定版本。
            subprocess.run(["corepack", "enable", "--install-directory", temporary], check=True)
            env["PATH"] = temporary + os.pathsep + env["PATH"]
            with open(Path(temporary) / "api.log", "w+") as log:
                api = subprocess.Popen(
                    [
                        "uv",
                        "run",
                        "--all-packages",
                        "uvicorn",
                        "tests.run_knowledge_browser:create_browser_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                        "--no-access-log",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                for _ in range(100):
                    if api.poll() is not None:
                        raise RuntimeError("隔离 API 启动失败")
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{api_port}/health/live", timeout=0.3
                        ) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("隔离 API 未就绪")
                print(
                    "空隔离 PG 已迁移至 head，真实 API 已就绪；运行 Chromium 知识治理验收。",
                    flush=True,
                )
                command = [
                    "corepack",
                    "pnpm",
                    "--dir",
                    "apps/web_chat",
                    "exec",
                    "playwright",
                    "test",
                    "--config",
                    "playwright.knowledge.config.ts",
                    *sys.argv[1:],
                ]
                result = subprocess.run(command, cwd=ROOT, env=env)
                if result.returncode:
                    log.seek(0)
                    output = (
                        log.read()
                        .replace(pg_password, "[test-secret]")
                        .replace(password, "[test-password]")
                    )
                    print(output[-5000:])
                return result.returncode
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
        subprocess.run(
            ["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("本轮临时 API/PostgreSQL 已清理。", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

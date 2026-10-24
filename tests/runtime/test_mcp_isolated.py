"""显式启用的真实容器/stdio MCP 契约测试，不连接业务系统。"""

import json
import os
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from supportops_core.mcp_isolated_contracts import (
    ISOLATED_ENV,
    IsolatedMcpReport,
    IsolatedServiceStatus,
    mock_descriptor,
)
from supportops_core.mcp_isolated_runtime import source_fingerprint
from supportops_core.tool_discovery_contracts import discovery_digest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/runtime/mcp-isolated.compose.yml"


@dataclass(frozen=True)
class IsolatedCompose:
    environment: dict[str, str]
    project: str

    def command(self, override: Path | None = None) -> list[str]:
        command = [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "--project-name",
            self.project,
            "-f",
            str(COMPOSE),
        ]
        if override is not None:
            command += ["-f", str(override)]
        return command + ["--profile", "mcp-isolated"]

    def run(
        self,
        *arguments: str,
        options: tuple[str, ...] = (),
        override: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        name = f"{self.project}-{uuid4().hex[:8]}"
        try:
            return subprocess.run(
                self.command(override)
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--name",
                    name,
                    *options,
                    "mcp-isolated",
                    *arguments,
                ],
                cwd=ROOT,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        finally:
            # 超时也仅清理本次随机命名的容器，不接触开发服务。
            subprocess.run(
                ["docker", "rm", "--force", name],
                env=self.environment,
                capture_output=True,
                timeout=15,
                check=False,
            )

    def python(self, source: str) -> subprocess.CompletedProcess[str]:
        return self.run(
            "-i",
            *(f"{key}={value}" for key, value in ISOLATED_ENV.items()),
            "/opt/supportops/.venv/bin/python",
            "-I",
            "-B",
            "-c",
            source,
            options=("--entrypoint", "/usr/bin/env"),
        )


@pytest.fixture(scope="module")
def isolated_compose() -> Iterator[IsolatedCompose]:
    if os.getenv("SUPPORTOPS_RUN_MCP_TESTS") != "1":
        pytest.skip("需显式启用已构建隔离 Mock 镜像的容器测试")
    environment = dict(os.environ)
    for name in ("SUPPORTOPS_MCP_ISOLATED_IMAGE", "SUPPORTOPS_PYTHON_IMAGE", "SUPPORTOPS_UV_IMAGE"):
        value = environment.get(name, "")
        assert re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", value), f"{name} 须为不可变引用"
    yield IsolatedCompose(environment, f"supportops-mcp-test-{uuid4().hex[:10]}")


def output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert len(result.stdout.encode()) < 16_384
    assert len(result.stdout.splitlines()) == 1
    return json.loads(result.stdout)


def assert_failure(result: subprocess.CompletedProcess[str], code: str, exit_code: int) -> None:
    assert result.returncode == exit_code, (result.returncode, result.stdout, result.stderr)
    assert json.loads(result.stdout) == {
        "schema_version": "isolated_mcp_failure_v1",
        "error_code": code,
        "result_confirmed": False,
        "business_execution_enabled": False,
    }


@pytest.mark.parametrize("operation", ["catalog", "read-status"])
def test_real_stdio_handshake_catalog_and_read_only_call(
    isolated_compose: IsolatedCompose,
    operation: str,
) -> None:
    report = IsolatedMcpReport.model_validate(output(isolated_compose.run(operation)))
    descriptor = mock_descriptor()
    assert report.operation == operation.replace("-", "_")
    assert report.catalog.tools == [descriptor]
    assert report.source_fingerprint == source_fingerprint()
    assert report.descriptor_digest == discovery_digest(descriptor.model_dump(mode="json"))
    assert report.input_schema_digest == discovery_digest(descriptor.input_schema)
    assert report.output_schema_digest == discovery_digest(descriptor.output_schema)
    assert report.observation == (IsolatedServiceStatus() if operation == "read-status" else None)
    assert report.business_execution_enabled is False


def test_compose_keeps_no_network_no_business_mounts_and_resource_limits(
    isolated_compose: IsolatedCompose,
) -> None:
    result = subprocess.run(
        isolated_compose.command() + ["config", "--format", "json"],
        env=isolated_compose.environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    service = json.loads(result.stdout)["services"]["mcp-isolated"]
    assert service["user"] == "10002:10002"
    assert service["network_mode"] == "none" and service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert not any(service.get(key) for key in ("volumes", "ports", "environment", "env_file"))
    assert service["pids_limit"] == 16
    assert int(service["mem_limit"]) == int(service["memswap_limit"]) == 256 * 1024 * 1024
    assert float(service["cpus"]) == 0.5
    assert service["ulimits"]["nofile"] == {"soft": 64, "hard": 64}
    # Compose JSON 将零值字段省略；另一个用例核对容器内实际的 (0, 0)。
    assert all(service["ulimits"]["core"].get(key, 0) == 0 for key in ("soft", "hard"))
    assert service["ulimits"]["cpu"] == {"soft": 10, "hard": 12}
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,size=8m,mode=1777"]


def test_live_container_enforces_identity_environment_filesystem_and_network(
    isolated_compose: IsolatedCompose,
) -> None:
    result = isolated_compose.python("""
import errno, json, os, resource, socket, tempfile
from pathlib import Path
from supportops_core.mcp_isolated_runtime import require_isolated_runtime
require_isolated_runtime()
try:
    Path("/opt/supportops/isolation-write-probe").write_text("test")
except OSError as exc:
    assert exc.errno == errno.EROFS
else:
    raise AssertionError("root filesystem is writable")
with tempfile.TemporaryFile(dir="/tmp") as handle:
    handle.write(b"temporary test data")
with socket.socket() as connection:
    connection.settimeout(0.1)
    try:
        connection.connect(("192.0.2.1", 443))
    except OSError as exc:
        assert exc.errno == errno.ENETUNREACH
    else:
        raise AssertionError("unexpected outbound connection")
assert not Path("/var/run/docker.sock").exists()
assert not Path("/opt/supportops/.env").exists()
assert not Path("/opt/supportops/apps").exists()
print(json.dumps({"uid": os.geteuid(), "gid": os.getegid(), "environment": dict(os.environ),
    "interfaces": [name for _, name in socket.if_nameindex()],
    "nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
    "core": resource.getrlimit(resource.RLIMIT_CORE),
    "cpu": resource.getrlimit(resource.RLIMIT_CPU)}))
""")
    assert output(result) == {
        "uid": 10002,
        "gid": 10002,
        "environment": ISOLATED_ENV,
        "interfaces": ["lo"],
        "nofile": [64, 64],
        "core": [0, 0],
        "cpu": [10, 12],
    }


@pytest.mark.parametrize("identity", ["0:0", "10001:10001"])
def test_other_os_identity_is_rejected_before_sdk(
    isolated_compose: IsolatedCompose,
    identity: str,
) -> None:
    assert_failure(
        isolated_compose.run("catalog", options=("--user", identity)),
        "MCP_ISOLATED_RUNTIME_REQUIRED",
        3,
    )


def test_network_enabled_runtime_is_rejected(
    isolated_compose: IsolatedCompose,
    tmp_path: Path,
) -> None:
    override = tmp_path / "network.json"
    override.write_text(json.dumps({"services": {"mcp-isolated": {"network_mode": "bridge"}}}))
    assert_failure(
        isolated_compose.run("catalog", override=override),
        "MCP_ISOLATED_RUNTIME_REQUIRED",
        3,
    )


def test_bypassing_environment_scrub_is_rejected(isolated_compose: IsolatedCompose) -> None:
    assert_failure(
        isolated_compose.run(
            "-I",
            "-B",
            "-m",
            "supportops_core.mcp_isolated_cli",
            "catalog",
            options=("--entrypoint", "/opt/supportops/.venv/bin/python"),
        ),
        "MCP_ISOLATED_RUNTIME_REQUIRED",
        3,
    )


def test_injected_application_environment_does_not_reach_protocol(
    isolated_compose: IsolatedCompose,
) -> None:
    result = isolated_compose.run(
        "read-status",
        options=("--env", "SUPPORTOPS_DATABASE_URL=forbidden-test-sentinel"),
    )
    report = IsolatedMcpReport.model_validate(output(result))
    assert report.observation == IsolatedServiceStatus()
    assert "forbidden-test-sentinel" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "arguments", [("unknown",), ("read-status", "extra"), ("catalog", "https://example.invalid")]
)
def test_cli_rejects_arbitrary_commands_and_arguments(
    isolated_compose: IsolatedCompose,
    arguments: tuple[str, ...],
) -> None:
    assert_failure(isolated_compose.run(*arguments), "MCP_ISOLATED_ARGUMENTS_INVALID", 2)


@pytest.mark.parametrize("case", ["unknown_tool", "extra_argument", "cursor"])
def test_real_server_rejects_calls_outside_fixed_contract(
    isolated_compose: IsolatedCompose,
    case: str,
) -> None:
    source = """
import asyncio, json, logging, os, sys
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters, McpError
from mcp.client.stdio import stdio_client
from supportops_core.mcp_isolated_contracts import ISOLATED_ENV, MOCK_TOOL_NAME, PROTOCOL_VERSION
from supportops_core.mcp_isolated_runtime import require_isolated_runtime
logging.disable(logging.CRITICAL)
async def check(case):
    require_isolated_runtime()
    parameters = StdioServerParameters(command=sys.executable,
        args=["-I", "-B", "-m", "supportops_core.mcp_isolated_server"],
        env=dict(ISOLATED_ENV), cwd="/opt/supportops")
    with open(os.devnull, "w") as errors:
        async with stdio_client(parameters, errlog=errors) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing,
                read_timeout_seconds=timedelta(seconds=3)) as session:
                initialized = await session.initialize()
                assert initialized.protocolVersion == PROTOCOL_VERSION
                if case == "cursor":
                    try:
                        await session.list_tools(cursor="unexpected-cursor")
                    except McpError:
                        pass
                    else:
                        raise AssertionError("cursor accepted")
                else:
                    result = await session.call_tool(
                        "unknown_tool" if case == "unknown_tool" else MOCK_TOOL_NAME,
                        {} if case == "unknown_tool" else {"user_id": "forbidden"})
                    assert result.isError and result.structuredContent is None
    print(json.dumps({"rejected": True, "case": case}))
"""
    result = isolated_compose.python(source + f"\nasyncio.run(check({case!r}))\n")
    assert output(result) == {"rejected": True, "case": case}

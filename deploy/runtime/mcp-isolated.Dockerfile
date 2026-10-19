# 独立的无网络Mock运行镜像，不用于真实连接器或默认API/Worker。
ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/supportops
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/agent_worker/pyproject.toml apps/agent_worker/pyproject.toml
COPY packages/support_core/pyproject.toml packages/support_core/pyproject.toml
RUN uv sync --frozen --package supportops-core --extra mcp-isolated --no-dev --no-install-workspace
COPY packages/support_core/src packages/support_core/src
RUN uv sync --frozen --package supportops-core --extra mcp-isolated --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS isolated
WORKDIR /opt/supportops
COPY --from=builder /opt/supportops/.venv /opt/supportops/.venv
RUN groupadd --gid 10001 gateway && groupadd --gid 10002 connector && useradd --uid 10002 --gid 10002 --groups gateway --no-create-home connector \
    && install -d -m 0770 -o 10002 -g 10001 /run/supportops-mcp
USER 10002:10002
# 清除基础镜像或误传的环境；SDK子进程只能继承相同固定非秘密环境。
ENTRYPOINT ["/usr/bin/env", "-i", "PATH=/opt/supportops/.venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8", "SUPPORTOPS_ISOLATED_MCP_MODE=mock-only", "/opt/supportops/.venv/bin/python", "-I", "-B", "-m", "supportops_core.mcp_isolated_cli"]
CMD ["catalog"]

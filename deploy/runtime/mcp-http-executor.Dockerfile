# 单次HTTP执行器；不包含应用源码、数据库配置或真实凭据。
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

FROM ${PYTHON_IMAGE} AS executor
WORKDIR /
COPY --from=builder /opt/supportops/.venv /opt/supportops/.venv
RUN groupadd --gid 10001 gateway && groupadd --gid 10004 http-executor \
    && useradd --uid 10004 --gid 10004 --groups gateway --no-create-home http-executor \
    && install -d -m 0755 -o 0 -g 0 /etc/supportops-mcp \
    && install -d -m 0770 -o 10004 -g 10001 /run/supportops-mcp-executor
USER 10004:10004
ENTRYPOINT ["/usr/bin/env", "-i", "PATH=/opt/supportops/.venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8", "SUPPORTOPS_HTTP_MCP_MODE=one-shot-v1", "/opt/supportops/.venv/bin/python", "-I", "-B", "-m", "supportops_core.mcp_http_executor_cli"]
CMD ["call"]

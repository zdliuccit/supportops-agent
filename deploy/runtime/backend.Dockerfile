# 基础镜像由发布维护者提供经批准的不可变 digest，禁止在这里猜测生产版本。
ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS backend
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/agent_worker/pyproject.toml apps/agent_worker/pyproject.toml
COPY packages/support_core/pyproject.toml packages/support_core/pyproject.toml
RUN uv sync --frozen --all-packages --no-dev --no-install-workspace
COPY apps/api/src apps/api/src
COPY apps/agent_worker/src apps/agent_worker/src
COPY packages/support_core/src packages/support_core/src
RUN uv sync --frozen --all-packages --no-dev --no-editable
COPY alembic.ini ./
COPY migrations migrations
COPY deploy/runtime/migrate.py deploy/runtime/migrate.py
COPY deploy/runtime/preflight.py deploy/runtime/preflight.py
RUN groupadd --gid 10001 supportops && useradd --uid 10001 --gid 10001 --no-create-home supportops
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["supportops-api"]

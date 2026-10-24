ARG SUPPORTOPS_TEST_UV_IMAGE
FROM ${SUPPORTOPS_TEST_UV_IMAGE}

WORKDIR /workspace
COPY --chown=10001:10001 . /workspace
RUN uv sync --locked --all-packages --group dev

USER 10001:10001
CMD ["uv", "run", "--locked", "pytest"]

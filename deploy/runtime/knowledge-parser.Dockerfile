# 仅固定Markdown解析源码和标准库；不安装应用、SDK、模型或数据库依赖。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS knowledge-parser
RUN groupadd --gid 10001 gateway && groupadd --gid 10005 parser \
    && useradd --uid 10005 --gid 10005 --groups gateway --no-create-home parser \
    && install -d -m 0755 -o 0 -g 0 /opt/parser/supportops_core \
    && install -d -m 0770 -o 10005 -g 10001 /run/supportops-knowledge-parser
COPY packages/support_core/src/supportops_core/__init__.py /opt/parser/supportops_core/__init__.py
COPY packages/support_core/src/supportops_core/knowledge_content.py /opt/parser/supportops_core/knowledge_content.py
COPY packages/support_core/src/supportops_core/knowledge_parser_isolated.py /opt/parser/supportops_core/knowledge_parser_isolated.py
WORKDIR /
USER 10005:10005
ENTRYPOINT ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8", "SUPPORTOPS_KNOWLEDGE_PARSER_MODE=markdown-once-v1", "/usr/local/bin/python", "-I", "-S", "-B", "-c", "import sys;sys.path.insert(0,'/opt/parser');from supportops_core.knowledge_parser_isolated import main;main()"]
CMD []

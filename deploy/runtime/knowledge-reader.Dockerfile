# 独立来源读取器；只复制标准库入口/共享协议/规范化源码，不安装业务依赖。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS knowledge-reader
RUN groupadd --gid 10001 gateway && groupadd --gid 10006 reader \
    && useradd --uid 10006 --gid 10006 --groups gateway --no-create-home reader \
    && install -d -m 0755 -o 0 -g 0 /opt/reader/supportops_core /etc/supportops-knowledge /source \
    && install -d -m 0770 -o 10006 -g 10001 /run/supportops-knowledge-reader
COPY packages/support_core/src/supportops_core/__init__.py /opt/reader/supportops_core/__init__.py
COPY packages/support_core/src/supportops_core/knowledge_content.py /opt/reader/supportops_core/knowledge_content.py
COPY packages/support_core/src/supportops_core/knowledge_parser_isolated.py /opt/reader/supportops_core/knowledge_parser_isolated.py
COPY packages/support_core/src/supportops_core/knowledge_reader_isolated.py /opt/reader/supportops_core/knowledge_reader_isolated.py
WORKDIR /
USER 10006:10006
ENTRYPOINT ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8", "SUPPORTOPS_KNOWLEDGE_READER_MODE=markdown-once-v1", "/usr/local/bin/python", "-I", "-S", "-B", "-c", "import sys;sys.path.insert(0,'/opt/reader');from supportops_core.knowledge_reader_isolated import main;main()"]
CMD []

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DOCKER_MODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

# 镜像部署需要将运行所需源码写入镜像。Python 依赖在容器首次启动时安装。
COPY src/requirements-server.txt .
COPY src/app/__init__.py src/app/config_manager.py src/app/config_schema.py src/app/main.py /src/app/
COPY src/app/server /src/app/server
COPY src/app/ui /src/app/ui
COPY src/tasker /src/tasker
COPY src/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/bin/sh", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "app/main.py"]

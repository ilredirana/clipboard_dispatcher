#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "入口脚本必须以 root 用户启动" >&2
    exit 1
fi

mkdir -p /app/data

requirements_file=/src/requirements-server.txt
requirements_marker=/usr/local/lib/python3.11/site-packages/.clipboard-dispatcher-requirements.sha256
requirements_hash=$(sha256sum "$requirements_file" | awk '{print $1}')

if [ ! -f "$requirements_marker" ] || [ "$(cat "$requirements_marker")" != "$requirements_hash" ]; then
    echo "安装 Python 运行依赖"
    pip install --no-cache-dir --root-user-action=ignore -r "$requirements_file"
    printf '%s\n' "$requirements_hash" > "$requirements_marker"
fi

# Windows 与部分 NAS 的只读源码绑定目录仅允许 root 读取，应用进程保持 root 身份以确保可直接加载源码。
exec "$@"

"""校验应用版本与 Docker 默认镜像标签一致。"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r'^APP_VERSION\s*=\s*"(?P<version>\d+\.\d+(?:\.\d+)?)"$', re.MULTILINE)
IMAGE_TAG_PATTERN = re.compile(r"clipboard-dispatcher:(?P<version>\d+\.\d+(?:\.\d+)?)")


def extract_version(path: Path, pattern: re.Pattern[str]) -> str:
    """从指定文件读取唯一版本号。"""
    content = path.read_text(encoding="utf-8")
    matches = pattern.findall(content)
    if len(matches) != 1:
        raise RuntimeError(f"版本字段数量异常：{path}，期望 1 个，实际 {len(matches)} 个")
    return matches[0]


def extract_image_version(path: Path) -> str:
    """从 Compose 文件读取默认镜像标签。"""
    content = path.read_text(encoding="utf-8")
    matches = IMAGE_TAG_PATTERN.findall(content)
    if len(matches) != 1:
        raise RuntimeError(f"默认镜像标签数量异常：{path}，期望 1 个，实际 {len(matches)} 个")
    return matches[0]


def main() -> int:
    """运行发布资源校验。"""
    app_version = extract_version(PROJECT_ROOT / "src" / "app" / "server" / "version.py", VERSION_PATTERN)
    compose_version = extract_image_version(PROJECT_ROOT / "src" / "docker-compose.image.yaml")
    if app_version != compose_version:
        raise RuntimeError(
            "发行版本不一致："
            f"应用={app_version}，Compose={compose_version}"
        )
    print(f"发行资源校验通过：{app_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

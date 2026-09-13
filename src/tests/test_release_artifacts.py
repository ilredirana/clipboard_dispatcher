"""验证 Windows 与 Docker 交付形态的静态发布资源。"""

import subprocess
import sys
from pathlib import Path


def test_release_artifacts_are_consistent() -> None:
    """发布校验脚本必须通过，避免 Windows 与 Docker 版本漂移。"""
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "validate_release.py")],
        cwd=project_root,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "发行资源校验通过：1.0.0" in result.stdout

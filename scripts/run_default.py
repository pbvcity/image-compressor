#!/usr/bin/env python3
"""一键准备环境、按默认设置压缩，并复核本次结果。仅使用标准库启动。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 10)


def command(args: list[str], root: Path) -> int:
    # 子进程统一 UTF-8，Windows 中文路径和日志也能正常显示。
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run(args, cwd=root, env=env, check=False).returncode


def ensure_environment(root: Path) -> Path:
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("首次运行：正在建立 Python 环境……", flush=True)
        if command([sys.executable, "-m", "venv", str(environment)], root):
            raise RuntimeError("无法建立虚拟环境。Linux 请先安装 Python 对应的 venv 软件包，然后重试。")

    # 不依赖 pip 启动脚本中的绝对路径，工具目录移动后仍使用当前解释器。
    probe = subprocess.run(
        [str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3, 10))"],
        cwd=root, capture_output=True, check=False)
    if probe.returncode:
        raise RuntimeError(".venv 环境不可用或 Python 版本过旧，请删除 .venv 后重新双击运行。")

    # 从依赖文件读取版本，避免启动入口与 requirements.txt 各维护一份。
    check = """\
from importlib.metadata import version
from pathlib import Path
from PIL import Image
for line in Path('requirements.txt').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#'):
        name, expected = line.split('==')
        if version(name.strip()) != expected.strip():
            raise SystemExit(1)
"""
    ready = subprocess.run([str(python), "-c", check], cwd=root,
                           capture_output=True, check=False).returncode == 0
    if not ready:
        print("正在安装项目依赖（首次安装需要联网）……", flush=True)
        if command([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(root / "requirements.txt")], root):
            raise RuntimeError("依赖安装失败，请检查网络及上方提示，然后重新运行。")
        if subprocess.run([str(python), "-c", check], cwd=root,
                          capture_output=True, check=False).returncode:
            raise RuntimeError("依赖无法加载，请删除 .venv 后重新运行。")
    return python


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print("需要 Python 3.10 或以上，请安装后重新运行。", flush=True)
        return 2
    source = ROOT / "input"
    source.mkdir(exist_ok=True)
    files = (p for p in source.rglob("*") if p.is_file() and not p.is_symlink()
             and p.suffix.lower() == ".png"
             and not any(part.startswith(".") for part in p.relative_to(source).parts))
    if next(files, None) is None:
        print(f"请先将 PNG 图片或图片文件夹放入：{source}\n放好后重新双击启动文件。", flush=True)
        return 0

    python = ensure_environment(ROOT)
    print("开始压缩：保留尺寸和透明度，不透明图转质量 90 JPG，覆盖同名输出。", flush=True)
    result = command([str(python), str(ROOT / "scripts/compress_images.py"), "--overwrite"], ROOT)
    if result not in (0, 1):
        print("压缩未能开始，请检查上方错误提示。", flush=True)
        return result
    # 即使部分图片压缩失败，也复核本次成功的图片并记录失败项。
    print("正在逐张复核本次结果……", flush=True)
    verified = command([str(python), str(ROOT / "scripts/verify_results.py")], ROOT)
    if result or verified:
        print(f"本次任务存在失败，请查看：{ROOT / 'reports'}", flush=True)
        return result or verified
    print(f"压缩和复核全部通过。\n结果图片：{ROOT / 'compressed'}\n本次报告：{ROOT / 'reports'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)

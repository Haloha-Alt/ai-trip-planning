# @author: 甲
# @reviewer: 乙
# @since: 2026-10-28
"""运行时环境与路径：所有"从哪读配置 / 往哪写结果"的答案都在这里。

为什么要单独一个文件
--------------------
Streamlit 的工作目录可能是任意位置，FastAPI 用 uvicorn 启动时又是另一个，
CLI 又是项目根。路径写死在哪一个入口里，另外两个就必然错。
所以统一由本文件用 `__file__` 反推项目根，谁都别自己拼路径。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 项目根
OUTPUT_DIR = ROOT / "outputs"
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"


def load_dotenv(path: Path | None = None) -> bool:
    """极简 .env 加载：读 KEY=VALUE 写进 os.environ，**不覆盖已存在的环境变量**。

    刻意不用 python-dotenv：
    这一层将来要能打包成单文件离线演示，能少一个依赖就少一个。
    真环境变量优先，是因为 CI / 部署环境通常已经注入好了。
    """
    p = path or (ROOT / ".env")
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False

    loaded = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded = True
    return loaded


def env(key: str, default: str = "") -> str:
    """读环境变量；空值返回默认。"""
    return (os.environ.get(key) or "").strip() or default


def env_bool(key: str, default: bool = False) -> bool:
    raw = env(key, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def rel(path: Path | str) -> str:
    """把绝对路径显示成相对项目根的形式，日志与界面上都好读。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return str(path)

"""后端入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_venv() -> None:
    """当前解释器缺少依赖时，自动改用项目虚拟环境重新启动本脚本。

    解决直接点击 IDE 运行（默认使用系统 Python）时报 fastapi 缺失的问题。
    """
    try:
        import fastapi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    print(f"[bootstrap] 切换项目虚拟环境 Python: {venv_python}", flush=True)
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_bootstrap_venv()

import uvicorn

from api.routes import app


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

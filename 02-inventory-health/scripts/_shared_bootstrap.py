#!/usr/bin/env python3
"""共享底座定位（_shared/ 不是技能，用绝对路径引用，不写死 ../_shared/）。

解析优先级：
  1. 显式传入 shared_root=
  2. 环境变量 SELLFOX_SHARED_ROOT
  3. 本技能 config/paths.yaml 的 shared_root
  4. 脚本相对路径兜底（scripts/../.. /_shared）

所有脚本在导入 config_loader / sellfox_offline 之前先调用 add_shared_to_path()
（v3：计算面零网络，不再导入旧 sellfox_client —— 取数面由 agent 调客户端 MCP 完成）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _from_paths_yaml(cfg_path: Path) -> str | None:
    if not cfg_path.exists():
        return None
    for raw in cfg_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("shared_root:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            return val or None
    return None


def shared_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("SELLFOX_SHARED_ROOT")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent  # .../scripts
    found = _from_paths_yaml(here.parent / "config" / "paths.yaml")
    if found:
        return Path(found).expanduser()
    bundled = here.parent / "_shared"
    return bundled if bundled.is_dir() else here.parent.parent / "_shared"


def add_shared_to_path(explicit: str | None = None) -> Path:
    root = shared_root(explicit)
    sp = str(root / "scripts")
    if sp not in sys.path:
        sys.path.insert(0, sp)
    return root


if __name__ == "__main__":
    print(add_shared_to_path())
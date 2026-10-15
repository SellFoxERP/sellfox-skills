#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径解析与共享底座加载（04-weekly-review）。

为什么需要它
------------
技能可能被安装到任意位置，脚本里**绝不能写死** ``../_shared/``。本模块按优先级解析
``shared_root``，并把 ``_shared/scripts`` 注入 ``sys.path``，让技能脚本可以直接
``import config_loader`` / ``feishu_writer`` / ``sellfox_offline`` / ``validate_raw``
（v3 计算面组件；旧直连客户端 ``sellfox_client.py`` 已废弃，不再 import）。

解析优先级：
    1) 环境变量 ``SELLFOX_SHARED_ROOT``
    2) 本技能 ``config/paths.yaml`` 的 ``shared_root``
    3) 与 ``_shared`` 同级时的自动推断（``<skill>/../_shared``）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

SKILL_ROOT = Path(__file__).resolve().parent.parent          # 04-weekly-review/
SKILL_CONFIG_DIR = SKILL_ROOT / "config"
SKILL_REFERENCES_DIR = SKILL_ROOT / "references"


def _load_mini_yaml(path: Path) -> Dict[str, Any]:
    """读取 paths.yaml 的极简解析（仅需顶层 key: value，不引入外部依赖）。"""
    data: Dict[str, Any] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not raw.startswith((" ", "\t")):          # 只取顶层键
            data[key] = value
    return data


def resolve_shared_root(explicit: str | None = None) -> Path:
    """按优先级解析 ``_shared/`` 绝对路径，找不到就抛出可读错误。"""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("SELLFOX_SHARED_ROOT")
    if env:
        candidates.append(Path(env))
    cfg = _load_mini_yaml(SKILL_CONFIG_DIR / "paths.yaml")
    if cfg.get("shared_root"):
        candidates.append(Path(cfg["shared_root"]))
    candidates.append(SKILL_ROOT / "_shared")  # 独立发布包
    candidates.append(SKILL_ROOT.parent / "_shared")          # 默认目录布局

    for c in candidates:
        if c and (c.expanduser() / "scripts" / "config_loader.py").exists():
            return c.expanduser().resolve()

    tried = "\n".join(f"  - {c}" for c in candidates)
    raise FileNotFoundError(
        "未找到 _shared/（缺少 scripts/config_loader.py）。已尝试：\n"
        f"{tried}\n"
        "请检查 config/paths.yaml 的 shared_root，或设置环境变量 SELLFOX_SHARED_ROOT。"
    )


def bootstrap(explicit_shared_root: str | None = None) -> Path:
    """解析共享底座并把其 scripts 目录加入 sys.path。返回 shared_root。"""
    shared = resolve_shared_root(explicit_shared_root)
    scripts = str(shared / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return shared


def load_skill_config(name: str = "weekly") -> Dict[str, Any]:
    """加载本技能自有配置（config/<name>.yaml）。"""
    bootstrap()
    from config_loader import load_yaml_file  # type: ignore
    return load_yaml_file(SKILL_CONFIG_DIR / f"{name}.yaml")


if __name__ == "__main__":
    root = bootstrap()
    print(f"skill_root   = {SKILL_ROOT}")
    print(f"shared_root  = {root}")
    print(f"shared cfg   = {root / 'config'}")
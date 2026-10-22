#!/usr/bin/env python3
"""技能引导层 —— 定位 _shared 底座并挂载离线工具（v3：零网络，模式同 05）。

    from _bootstrap import SKILL_ROOT, shared_root, load_analysis_config
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

SKILL_ROOT = Path(__file__).resolve().parent.parent  # 13-sales-profit-analysis/
CONFIG_DIR = SKILL_ROOT / "config"


def _mini_yaml(path: Path) -> Dict[str, str]:
    """极简 YAML 顶层键值解析（仅用于定位 shared_root，零依赖）。"""
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line or line.startswith("- "):
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            data[key.strip()] = value
    return data


def _locate_shared() -> Path:
    """优先级：SELLFOX_SHARED_ROOT > config/paths.yaml > 源码同级 ../_shared > 包内 _sellfox_shared。"""
    for cand in (
        os.environ.get("SELLFOX_SHARED_ROOT", ""),
        _mini_yaml(CONFIG_DIR / "paths.yaml").get("shared_root", ""),
    ):
        if cand:
            p = Path(cand)
            if (p / "scripts" / "sellfox_offline.py").exists():
                return p
    sibling = SKILL_ROOT.parent / "_shared"
    if (sibling / "scripts" / "sellfox_offline.py").exists():
        return sibling
    sibling2 = SKILL_ROOT.parent / "_sellfox_shared"   # 批量安装态：技能目录旁
    if (sibling2 / "scripts" / "sellfox_offline.py").exists():
        return sibling2
    return SKILL_ROOT / "_sellfox_shared"


shared_root = _locate_shared()
sys.path.insert(0, str(shared_root / "scripts"))


def load_analysis_config() -> Dict[str, Any]:
    """读 config/analysis.yaml；有 PyYAML 用完整解析，缺失时用内置缩进解析。"""
    path = CONFIG_DIR / "analysis.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        return _indent_yaml(text)


def _indent_yaml(text: str) -> Dict[str, Any]:
    """内置缩进感知解析：支持嵌套映射 + 缩进列表（本技能配置形态足够）。"""
    root: Dict[str, Any] = {}
    # 栈元素：(缩进, 容器, 父容器, 在父中的键)
    stack = [(-1, root, None, None)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        ind, container, parent, pkey = stack[-1]
        if body.startswith("- "):
            if isinstance(container, dict) and not container and parent is not None:
                # 「key:」后跟列表项：把 dict 占位换成 list（栈缩进保持键所在层级）
                lst: list = []
                parent[pkey] = lst
                stack[-1] = (ind, lst, parent, pkey)
                container = lst
            if isinstance(container, list):
                container.append(_scalar(body[2:].strip()))
            continue
        key, _, value = body.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            child: Dict[str, Any] = {}
            container[key] = child
            stack.append((indent, child, container, key))
        else:
            container[key] = _scalar(value)
    return root


def _scalar(v: str) -> Any:
    v = v.strip('"').strip("'")
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v

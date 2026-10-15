#!/usr/bin/env python3
"""Listing 健康体检 —— 公共工具（路径引导 / 配置 / 数值规范化）。

为什么需要这个模块：技能可能被安装到任意位置，相对 ``../_shared/`` 会失效。
所以先读本技能 ``config/paths.yaml`` 的 ``shared_root``（为空时走同仓库同级兜底），
再把 ``_shared/scripts`` 加入 ``sys.path``，之后才能 import ``config_loader`` /
``sellfox_offline``（v3 计算面共享组件；旧直连 ``sellfox_client`` 已废弃，不得再 import）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_CONFIG_DIR = SKILL_DIR / "config"


# ---------------------------------------------------------------------------
# 路径引导
# ---------------------------------------------------------------------------

def resolve_shared_root() -> Path:
    """按优先级解析 ``_shared`` 根目录，并挂载 ``_shared/scripts`` 到 sys.path。"""
    explicit = os.environ.get("SELLFOX_SHARED_ROOT")
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    paths_yaml = SKILL_CONFIG_DIR / "paths.yaml"
    if paths_yaml.exists():
        for line in paths_yaml.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("shared_root:"):
                raw = line.split(":", 1)[1].strip().strip("'\"")
                if raw:
                    candidates.append(Path(raw).expanduser())
    candidates.append(SKILL_DIR / "_shared")  # 独立发布包
    candidates.append(SKILL_DIR.parent / "_shared")  # 同仓库布局的兜底

    for cand in candidates:
        if (cand / "scripts" / "config_loader.py").exists():
            root = cand.resolve()
            scripts = str(root / "scripts")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            return root
    # 最后尝试：把 paths.yaml 里的路径直接返回，交给 import 报清晰错误
    return (candidates[0] if candidates else SKILL_DIR.parent / "_shared").resolve()


SHARED_ROOT = resolve_shared_root()


def _shared_import():
    try:
        from config_loader import ConfigLoader  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            f"无法加载 _shared 脚本（shared_root={SHARED_ROOT}）：{exc}\n"
            f"请确认安装技能时一并放置 _shared/，并在 config/paths.yaml 填入正确的 shared_root。"
        )
    return ConfigLoader


def load_skill_config(name: str) -> Dict[str, Any]:
    """加载本技能 config/ 下的 yaml（用 _shared 的解析器，保持口径一致）。"""
    ConfigLoader = _shared_import()
    from config_loader import load_yaml_file  # type: ignore

    path = SKILL_CONFIG_DIR / (name if name.endswith((".yaml", ".yml")) else f"{name}.yaml")
    if not path.exists():
        return {}
    if name == "paths":
        return {}
    try:
        return load_yaml_file(path)
    except Exception:
        return {}


def load_shared_section(file_name: str, section: str) -> Dict[str, Any]:
    """读 _shared/config/<file>.yaml 的某 section（缺失返回空 dict）。"""
    ConfigLoader = _shared_import()
    try:
        loader = ConfigLoader(str(SHARED_ROOT))
        cfg = loader.load(file_name)
    except Exception:
        return {}
    value = cfg.get(section) if isinstance(cfg, dict) else None
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# 数值 / 字段规范化（字段值形态因工具而异：字符串、{"key":..}、数组）
# ---------------------------------------------------------------------------

def key_of(value: Any) -> Optional[str]:
    """从 ``{"key": "..."}`` / ``["..."]`` / 标量里取出可读值。"""
    if value is None:
        return None
    if isinstance(value, dict):
        for k in ("key", "value", "name", "labelName", "groupType"):
            if k in value and value[k] is not None:
                return str(value[k])
        return None
    if isinstance(value, (list, tuple)):
        parts = [key_of(v) for v in value]
        parts = [p for p in parts if p]
        return ",".join(parts) if parts else None
    return str(value)


def to_float(value: Any) -> Optional[float]:
    """尽力转 float；空/不可转返回 None（**不返回 0** —— 0 与「没拿到」是两回事）。"""
    v = key_of(value) if isinstance(value, (dict, list)) else value
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip().replace(",", "").replace("%", "")
    if text == "" or text.lower() in ("null", "none", "n/a", "-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def bsr_rank(record: Dict[str, Any]) -> Optional[float]:
    """取 ``bsrRank`` 里最小（最好）的名次；兼容 dict/数组形态。"""
    raw = record.get("bsrRank")
    ranks: List[float] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                r = to_float(item.get("rank"))
            else:
                r = to_float(item)
            if r is not None:
                ranks.append(r)
    elif isinstance(raw, dict):
        r = to_float(raw.get("rank"))
        if r is not None:
            ranks.append(r)
    return min(ranks) if ranks else None


def is_true(value: Any) -> Optional[bool]:
    """把 bool / "true" / "True" / 1 归一为 bool；未知返回 None。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return None


def record_id(record: Dict[str, Any]) -> str:
    """Listing 去重主键：优先 listingId，退化 shopId+asin，再退化 asin。"""
    lid = key_of(record.get("listingId"))
    if lid:
        return f"lid:{lid}"
    shop = key_of(record.get("shopId"))
    asin = key_of(record.get("asin") or record.get("parentAsin"))
    if asin:
        return f"{shop or '?'}:{asin}"
    return f"raw:{id(record)}"


def median(values: Sequence[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


# ---------------------------------------------------------------------------
# 严重度
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def severity_rank(level: str) -> int:
    return SEVERITY_ORDER.get(level, 9)


def sort_problems(problems: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        problems,
        key=lambda p: (severity_rank(p.get("severity", "P9")), p.get("asin") or "", p.get("check") or ""),
    )


# ---------------------------------------------------------------------------
# 演示环境 & 披露
# ---------------------------------------------------------------------------

def demo_fingerprint(*payloads: Any) -> Optional[str]:
    """识别演示环境指纹（MOCK- / SHOP-DEMO- / B0DEMO / 店铺名含「演示」等）。

    优先用 _shared 的 ``sellfox_offline.detect_demo_environment``（指纹清单统一维护）；
    加载失败时退化为本地正则兜底。
    """
    try:
        from sellfox_offline import detect_demo_environment  # type: ignore
    except Exception:
        detect_demo_environment = None  # type: ignore
    pattern = re.compile(r"(MOCK-|mock\.example\.com|demo\.example\.com|SHOP-DEMO-|B0DEMO|DPARENT|演示)")
    for payload in payloads:
        if payload is None:
            continue
        if detect_demo_environment is not None:
            try:
                fp = detect_demo_environment(payload)
            except Exception:
                fp = None
            if fp:
                return fp
        try:
            blob = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = str(payload)
        m = pattern.search(blob)
        if m:
            return m.group(1)
    return None


def write_json(path: str, payload: Dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
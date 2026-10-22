#!/usr/bin/env python3
"""评论 VOC 归因 —— 公共工具（路径引导 / 配置 / 数值规范化 / 评论脱敏，v3：零网络）。

为什么需要这个模块：技能可能被安装到任意位置，相对 ``../_shared/`` 会失效。
所以先读本技能 ``config/paths.yaml`` 的 ``shared_root``（绝对路径），再把
``_shared/scripts`` 加入 ``sys.path``，之后才能 import ``config_loader`` /
``sellfox_offline``（旧 ``sellfox_client`` 已废弃，v3 计算面**不 import** 它；
取数面 = agent 调 ``mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute}`` 并落盘 ``out/raw/``）。

本模块还提供：
- 评论者昵称掩码（评论原文含 PII，写外部文档前必须脱敏，铁律 8）；
- 演示环境指纹识别（铁律 11，指纹集来自 ``sellfox_offline.DEMO_FINGERPRINTS``）；
- 阈值读取的**单一真源**约定（本技能 config + _shared/config 合并）。
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
OUT_DIR = SKILL_DIR / "out"


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
    _shared_import()
    from config_loader import load_yaml_file  # type: ignore

    if name == "paths":
        return {}
    path = SKILL_CONFIG_DIR / (name if name.endswith((".yaml", ".yml")) else f"{name}.yaml")
    if not path.exists():
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


def asin_of(record: Dict[str, Any]) -> Optional[str]:
    """取 ASIN（评论源与赛狐共用主键）；退化到 parentAsin。"""
    return key_of(record.get("asin") or record.get("parentAsin"))


def record_id(record: Dict[str, Any]) -> str:
    """问题 ASIN 去重主键：shopId + asin（同 ASIN 跨店是不同对象）。"""
    shop = key_of(record.get("shopId"))
    asin = asin_of(record)
    if asin:
        return f"{shop or '?'}:{asin}"
    return f"raw:{id(record)}"


# ---------------------------------------------------------------------------
# 演示环境识别（v3：优先用 _shared/scripts/sellfox_offline 的指纹集）
# ---------------------------------------------------------------------------

#: ``sellfox_offline`` 不可用时的本地兜底指纹（与其 DEMO_FINGERPRINTS 保持一致）。
_LOCAL_FINGERPRINT_RE = re.compile(
    r"(MOCK-|mock\.example\.com|demo\.example\.com|SHOP-DEMO-|B0DEMO|DPARENT|演示)"
)


def demo_fingerprint(*payloads: Any) -> Optional[str]:
    """运行时识别演示环境指纹（MOCK- / demo.example.com / SHOP-DEMO- / B0DEMO / 含「演示」等）。

    优先调 ``_shared/scripts/sellfox_offline.detect_demo_environment``（v3 计算面共享组件，
    零网络）；不可用时退化为本地正则（指纹集一致）。
    """
    detect = None
    try:
        from sellfox_offline import detect_demo_environment as detect  # type: ignore
    except Exception:
        detect = None
    for payload in payloads:
        if payload is None:
            continue
        if detect is not None:
            try:
                fp = detect(payload)
            except Exception:
                fp = None
            if fp:
                return fp
        try:
            blob = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = str(payload)
        m = _LOCAL_FINGERPRINT_RE.search(blob)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 评论 PII 脱敏（铁律 8：写入外部文档前必须脱敏）
# ---------------------------------------------------------------------------

_NAME_PATTERNS = (
    re.compile(r"^(.{1})[^@\s]{0,}(.{1})$"),  # 通用：留首尾字符
)


def mask_reviewer(name: Any) -> Optional[str]:
    """掩码评论者昵称。保留首尾字符，中间以 * 代替。

    例：``"JohnSmith42"`` → ``"J**********2"``。
    空值返回 None（不写 0/空串占位）。
    """
    text = "" if name is None else str(name).strip()
    if not text:
        return None
    if len(text) <= 2:
        return text[0] + "*"
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


def mask_review(review: Dict[str, Any]) -> Dict[str, Any]:
    """对单条评论做结构级脱敏（掩码 reviewer_name；保留 reviewer_id 做计数）。"""
    out = dict(review)
    if "reviewer_name" in out:
        out["reviewer_name"] = mask_reviewer(out.get("reviewer_name"))
    return out


def mask_reviews_in_text(text: str, names: Sequence[str]) -> str:
    """文本兜底：把已知昵称替换为掩码（评论正文里若出现昵称）。"""
    if not text:
        return text
    result = text
    for n in names:
        if not n:
            continue
        masked = mask_reviewer(n)
        if masked and masked != n:
            result = result.replace(str(n), masked)
    return result


# ---------------------------------------------------------------------------
# 主题分类
# ---------------------------------------------------------------------------

def compile_theme_keywords(themes_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """把 config/voc.yaml → themes 编译成 ``{theme: [关键词小写]}``。"""
    compiled: Dict[str, List[str]] = {}
    for key, spec in (themes_cfg or {}).items():
        kws = (spec or {}).get("keywords") or []
        compiled[key] = [str(k).lower() for k in kws if str(k).strip()]
    return compiled


def theme_label(themes_cfg: Dict[str, Any], key: str) -> str:
    """主题 slug → 中文标签（未配置则回退 slug 本身）。"""
    spec = (themes_cfg or {}).get(key) or {}
    return str(spec.get("label") or key)


def match_themes(text: str, compiled: Dict[str, List[str]]) -> List[str]:
    """规则初筛：返回文本命中的主题 slug 列表（可能多主题）。

    这只是**可解释的兜底**；语义归因由 AI 助手 阅读原文完成（见 references/workflow.md）。
    未命中任何主题时归入 ``other``（不丢弃单条差评）。
    """
    low = (text or "").lower()
    hits: List[str] = []
    for theme, kws in compiled.items():
        if theme == "other":
            continue
        for kw in kws:
            if kw and kw in low:
                hits.append(theme)
                break
    return hits or ["other"]


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def write_json(path: str, payload: Any) -> None:
    p = Path(path)
    if not p.is_absolute():
        p = SKILL_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.is_absolute():
        p = SKILL_DIR / p
    return json.loads(p.read_text(encoding="utf-8"))


# 说明：v3 起本模块不再提供 new_client() —— 赛狐取数由 agent 调客户端元工具
# mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute} 并落盘 out/raw/ 完成（见
# _shared/SELLFOX_CLIENT.md）；计算面脚本零 HTTP、零 Key、不 import sellfox_client。

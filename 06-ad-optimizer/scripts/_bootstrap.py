#!/usr/bin/env python3
"""技能引导层 —— 解析配置、挂载 _shared 底座、提供统一入口（v3：零网络）。

所有技能内脚本都从这里导入，避免各自写死 ``../_shared/``：

    from _bootstrap import (SKILL_ROOT, load_thresholds, load_optimizer,
                            ad_changes_path, output_path, raw_dir)

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调客户端配置的 ``mcp__Sellfox-MCP__{help,search,action}``，
    按 `references/workflow.md` 的**取数计划**把每步信封落盘到 `out/raw/`；
    **写操作也由 agent 按审批流程逐项调 action**，脚本只提供提案构造与记录/校验 helper。
  * **计算面 = 脚本**：只读写本地文件，零网络。旧 `_shared/scripts/sellfox_client.py`
    已废弃，**本技能任何脚本不得 import 它**；离线工具改用 `_shared/scripts/sellfox_offline.py`。

设计原因：技能被安装到别处时相对路径会失效，所以 ``shared_root`` 必须来自
``config/paths.yaml`` 的绝对路径（或环境变量 ``SELLFOX_SHARED_ROOT``）。
本文件在导入阶段用极简行解析读取 paths.yaml，因此**零第三方依赖**也能定位底座；
定位后再用底座里的 ConfigLoader 读其余 YAML。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# --- 技能内固定路径 ---------------------------------------------------------
SKILL_ROOT = Path(__file__).resolve().parent.parent  # 06-ad-optimizer/
CONFIG_DIR = SKILL_ROOT / "config"
PATHS_FILE = CONFIG_DIR / "paths.yaml"


def _mini_load(path: Path) -> Dict[str, str]:
    """极简 YAML 顶层键值解析（只用于定位 shared_root，避免引导阶段引入依赖）。"""
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


PATHS: Dict[str, str] = _mini_load(PATHS_FILE)

# shared_root 解析优先级：环境变量 > paths.yaml > 同级默认 _shared（最后兜底）
SHARED_ROOT = Path(
    os.environ.get("SELLFOX_SHARED_ROOT")
    or PATHS.get("shared_root")
    or str(SKILL_ROOT / "_shared" if (SKILL_ROOT / "_shared").is_dir() else SKILL_ROOT.parent / "_shared")
).expanduser().resolve()

_SHARED_SCRIPTS = SHARED_ROOT / "scripts"
if str(_SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SHARED_SCRIPTS))


# --- 底座导入 ---------------------------------------------------------------

def _require_shared() -> None:
    """确认 _shared 底座就位，给出可操作的修复提示。

    v3 起不再依赖 ``sellfox_client.py``（已废弃）；计算面底座是
    ``sellfox_offline.py``（信封/演示指纹）+ ``config_loader.py`` + ``approval.py``。
    """
    missing = [
        p for p in ("sellfox_offline.py", "config_loader.py", "approval.py")
        if not (_SHARED_SCRIPTS / p).exists()
    ]
    if missing:
        raise RuntimeError(
            f"未找到 _shared 底座脚本 {missing}，检查 config/paths.yaml 的 shared_root：{SHARED_ROOT}\n"
            "安装本技能时必须一并放置 _shared/ 目录。"
        )


_require_shared()

from config_loader import ConfigLoader, load_yaml_file  # noqa: E402
from sellfox_offline import detect_demo_environment  # noqa: E402,F401


# --- 配置读取 ---------------------------------------------------------------

_loader: Optional[ConfigLoader] = None


def loader() -> ConfigLoader:
    """返回共享配置加载器（shared_root 已按 paths.yaml 固定）。"""
    global _loader
    if _loader is None:
        _loader = ConfigLoader(shared_root=str(SHARED_ROOT))
    return _loader


def load_thresholds() -> Dict[str, Any]:
    """读 _shared/config/thresholds.yaml（含 ad 与 approval 两段）。"""
    return loader().load("thresholds")


def load_shops() -> Dict[str, Any]:
    """读 _shared/config/shops.yaml（店铺与负责人）。"""
    return loader().load("shops")


def load_reports() -> Dict[str, Any]:
    """读 _shared/config/reports.yaml（report_id 一等前置）。"""
    return loader().load("reports")


def load_optimizer() -> Dict[str, Any]:
    """读本技能 config/optimizer.yaml。"""
    return load_yaml_file(CONFIG_DIR / "optimizer.yaml")


# --- 输出 / 日志路径 --------------------------------------------------------

def _resolve(rel_or_abs: str, default_rel: str) -> Path:
    raw = rel_or_abs or default_rel
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (SKILL_ROOT / p)


def output_path(name: str) -> Path:
    """技能输出文件路径（默认 out/ 下，全库统一）。"""
    out_dir = _resolve(PATHS.get("output_dir", ""), "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / name


def raw_dir(raw_arg: Optional[str] = None) -> Path:
    """agent 落盘目录（默认 <skill>/out/raw，可被参数覆盖；相对路径基于技能根）。"""
    p = _resolve(raw_arg or "", str(Path("out") / "raw"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def ad_changes_path() -> Path:
    """变更日志路径（累计闸与回滚读它）。"""
    log = _resolve(PATHS.get("ad_changes_log", ""), "logs/ad_changes.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    return log


def log_path(name: str) -> Path:
    """技能日志文件路径（默认 logs/ 下）。"""
    d = _resolve(PATHS.get("log_dir", ""), "logs")
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def demo_banner(payload: Any) -> str:
    """演示环境水印（命中指纹返回警示行，否则空串）。"""
    fp = detect_demo_environment(payload)
    if fp:
        return f"⚠️ 演示数据，非真实经营数据（指纹：{fp}）。本技能默认阻断写飞书与广告写操作。"
    return ""


if __name__ == "__main__":  # pragma: no cover
    print(f"SKILL_ROOT  = {SKILL_ROOT}")
    print(f"SHARED_ROOT = {SHARED_ROOT}")
    print(f"thresholds.ad = {load_thresholds().get('ad')}")
    print(f"ad_changes   = {ad_changes_path()}")
    print(f"raw_dir      = {raw_dir()}")

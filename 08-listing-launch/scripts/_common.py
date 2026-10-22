"""技能 08-listing-launch 脚本公共引导。

职责：
1. 定位 `_shared/`（读本技能 `config/paths.yaml` 的 ``shared_root``，或环境变量
   ``SELLFOX_SHARED_ROOT``），把它加入 ``sys.path`` —— **不写死相对路径**。
2. 构造 ``ConfigLoader``（含本技能配置）。v3 起**脚本零网络**：不再构造任何
   MCP 客户端（旧 ``sellfox_client`` 已废弃）；取数面 = agent 调
   ``mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute}`` 并落盘，纪律见
   ``_shared/SELLFOX_CLIENT.md`` v2；计算面共享组件见 ``_shared/scripts/sellfox_offline.py``。
3. 提供统一的输出/警告收集结构，供各步骤拼装上架包。

所有脚本都从这里 import，避免各自重复实现。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 技能根目录（scripts/ 的上一级）。
SKILL_ROOT = Path(__file__).resolve().parent.parent
#: 本技能配置目录。
CONFIG_DIR = SKILL_ROOT / "config"


class LaunchError(RuntimeError):
    """技能 08 的通用错误。"""


# ---------------------------------------------------------------------------
# 定位 _shared/
# ---------------------------------------------------------------------------


def _shared_root_from_paths_yaml() -> Optional[str]:
    """不依赖 YAML 库，从 config/paths.yaml 里取 ``shared_root`` 标量。"""
    paths_yaml = CONFIG_DIR / "paths.yaml"
    if not paths_yaml.exists():
        return None
    for raw in paths_yaml.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("shared_root:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    return None


def resolve_shared_root(explicit: Optional[str] = None) -> Path:
    """按优先级解析 _shared/ 根目录，找不到则报错（不静默兜底到错误的路径）。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("SELLFOX_SHARED_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    from_yaml = _shared_root_from_paths_yaml()
    if from_yaml:
        return Path(from_yaml).expanduser().resolve()
    # 最后兜底：假设 _shared 与本技能同级。
    bundled = SKILL_ROOT / "_shared"
    fallback = bundled if bundled.is_dir() else SKILL_ROOT.parent / "_shared"
    if fallback.exists():
        return fallback.resolve()
    raise LaunchError(
        "无法定位 _shared/：请在 config/paths.yaml 填写 shared_root，"
        "或设置环境变量 SELLFOX_SHARED_ROOT，并确保 _shared/ 已随技能一并安装。"
    )


def add_shared_to_path(shared_root: Path) -> None:
    scripts = str(shared_root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def ensure_shared_path() -> bool:
    """尽力把 _shared/scripts 加入 sys.path（供单独运行某脚本时也能 import）。

    定位失败时返回 False —— 调用方应据此给出友好提示，而不是抛栈。
    """
    try:
        add_shared_to_path(resolve_shared_root())
        return True
    except LaunchError:
        return False


def load_config(explicit_shared_root: Optional[str] = None):
    """返回 ``(shared_root, ConfigLoader)``。"""
    root = resolve_shared_root(explicit_shared_root)
    add_shared_to_path(root)
    from config_loader import ConfigLoader  # type: ignore  # 由 _shared 提供

    return root, ConfigLoader(shared_root=str(root))


# ---------------------------------------------------------------------------
# 技能配置读取（带默认与来源标注）
# ---------------------------------------------------------------------------


def load_launch_config(cfg) -> Dict[str, Any]:
    """读本技能 config/launch.yaml（ConfigLoader 的 shared_root 指向 _shared，
    故这里直接按技能自己的 config 目录读取），缺失项给出显式默认。"""
    ensure_shared_path()
    from config_loader import load_yaml_file  # type: ignore

    data = load_yaml_file(CONFIG_DIR / "launch.yaml")
    return data if isinstance(data, dict) else {}


def load_skill_yaml(name: str) -> Dict[str, Any]:
    """读取本技能 config/ 下的任意 yaml（listing_spec / compliance / launch）。"""
    ensure_shared_path()
    from config_loader import load_yaml_file  # type: ignore

    data = load_yaml_file(CONFIG_DIR / f"{name}.yaml")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 输出收集：把「结论 + 警告 + 需人工核查项」一路带下去
# ---------------------------------------------------------------------------


@dataclass
class Notes:
    """贯穿整个流程的提示集合。

    :param warnings: 需要醒目提示的风险（如演示环境、口径降级、截断）。
    :param manual_checks: 拿不到/未验证、必须人工核查的项（铁律 10）。
    :param sources: 每个关键参数的来源标注（配置命中/本次填写/默认值/需人工核查）。
    """

    warnings: List[str] = field(default_factory=list)
    manual_checks: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def need_manual(self, msg: str) -> None:
        if msg not in self.manual_checks:
            self.manual_checks.append(msg)

    def note_source(self, key: str, source: str) -> None:
        self.sources[key] = source

    def merge(self, other: "Notes") -> None:
        for w in other.warnings:
            self.warn(w)
        for m in other.manual_checks:
            self.need_manual(m)
        self.sources.update(other.sources)


# ---------------------------------------------------------------------------
# 字段取值：兼容「标量 / {"key": x} / ["x"]」三种服务端形态
# ---------------------------------------------------------------------------


def unwrap_value(value: Any) -> Any:
    """赛狐很多字段是 ``[{"key": "..."}]`` 或 ``["..."]`` 形态，统一取原始值。

    - ``{"key": v}`` → ``v``
    - ``[{"key": v}]`` / ``["v"]`` → 第一个元素
    - 其它原样返回；空列表/空 dict → ``None``（表达「无数据」）。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        if "key" in value:
            return value["key"]
        return value or None
    if isinstance(value, list):
        if not value:
            return None
        return unwrap_value(value[0])
    return value


def as_float(value: Any) -> Optional[float]:
    """尽力把值转 float；转不了返回 ``None``（不是 0 —— 区分「无数据」与 0）。"""
    v = unwrap_value(value)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
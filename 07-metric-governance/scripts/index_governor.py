#!/usr/bin/env python3
"""指标治理核心库（index_governor.py）—— 技能 07-metric-governance（v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，按
    `references/workflow.md` 的**取数计划**把每步完整信封落盘 `out/raw/`：
      - `stat_index_op1.json` / `stat_index_op2.json` ← `get_custom_report_stat_index_list`
        （`op_type=1/2` 各一次，新增/编辑两个视角）；
      - `custom_indices_<page>.json` ← `get_custom_report_index_page_list`
        （`share_status=0` 只取「我的」，逐页各落一盘）；
      - `custom_indices_verify_<page>.json` ← 写后回读（agent 执行完重取落盘）。
  * **本模块 = 计算面**：只读落盘 → 差异比对 → dry-run 提案 → 执行清单 / 记录 / 回读比对。
    全程零网络、纯标准库 + 共享底座（config_loader / approval / sellfox_offline）。

**脚本永不直接调写工具**（v3 硬规则）：`add/update_custom_report_index` 由 agent 按批准
逐项执行（协议见 workflow.md「写操作 agent 执行协议」），本模块只提供比对/提案/记录 helper：
  * :func:`build_execute_manifest` —— 生成「执行清单」（tool_id × 参数模板 × 旧公式快照；
    `contract_version` 置 ``null`` 占位，执行前由 agent 重取写契约再填）。
  * :func:`record_execution`      —— 把 agent 回传的执行结果追加变更日志（approval.record_changes）。
  * :func:`verify_against_rows`   —— 写后回读公式比对（一致 / 不一致 / 无法判定）。

其余设计要点：
  * 旧 `_shared/scripts/sellfox_client.py` 已废弃，本模块**不 import**（v3 硬规则）。
  * shared_root 从技能侧 config/paths.yaml 读取（不写死 ``../_shared/``）。
  * 列表字段 `data.rows`（分页类）；官方目录返回结构未实测 → 防御性解析，
    解析不到标「需人工核查」，不臆造节点。
  * **取数可信门**：现有指标落盘存在失败页/损坏页/截断时不生成提案——
    否则「指标其实在未取到的页上」会被误判为缺失，生成错误的新增提案（重复建指标）。
  * 演示指纹从落盘信封检测（sellfox_offline.detect_demo_environment）；命中默认只 dry-run。
  * 单次提案 ≤ thresholds.approval.max_items_per_batch；更新项写前快照旧公式（回滚依据）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 引导：解析 shared_root 并导入共享底座（不写死 ../_shared/）
# ---------------------------------------------------------------------------

_SKILL_DIR = Path(__file__).resolve().parent.parent  # <skill>/scripts/.. = <skill>


def _read_root_scalars(path: Path) -> Dict[str, str]:
    """读取 YAML 根层简单标量（仅够解析 paths.yaml 的 shared_root）。"""
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if len(line) - len(line.lstrip(" ")) == 0:  # 仅根层
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def resolve_shared_root(explicit: Optional[str] = None) -> Path:
    """解析 ``_shared/`` 绝对路径。

    优先级：显式参数 > 环境变量 ``SELLFOX_SHARED_ROOT`` > 技能 ``config/paths.yaml``
    > 相对兜底（同级 ``_shared``）。
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("SELLFOX_SHARED_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    for base in (_SKILL_DIR, Path.cwd()):
        data = _read_root_scalars(base / "config" / "paths.yaml")
        if data.get("shared_root"):
            return Path(data["shared_root"]).expanduser().resolve()
    bundled = _SKILL_DIR / "_shared"
    return (bundled if bundled.is_dir() else _SKILL_DIR.parent / "_shared").resolve()


SHARED_ROOT: Path = resolve_shared_root()
if not SHARED_ROOT.exists():
    raise SystemExit(
        f"_shared 路径不可用：{SHARED_ROOT}。请在 config/paths.yaml 设置 shared_root，"
        "或设置环境变量 SELLFOX_SHARED_ROOT，并确保已一并放置 _shared/。"
    )
_SHARED_SCRIPTS = str(SHARED_ROOT / "scripts")
if _SHARED_SCRIPTS not in sys.path:
    sys.path.insert(0, _SHARED_SCRIPTS)

import approval as ap  # noqa: E402
import config_loader as cfgmod  # noqa: E402
from sellfox_offline import detect_demo_environment, is_no_data, load_envelope  # noqa: E402


# ---------------------------------------------------------------------------
# 落盘文件名约定（与 references/workflow.md 的取数计划一一对应）
# ---------------------------------------------------------------------------

#: 官方指标目录两个视角的落盘名。
STAT_STEPS: Dict[str, str] = {"1": "stat_index_op1", "2": "stat_index_op2"}

#: 现有自定义指标（取数）/ 写后回读（验证）的落盘前缀。
_INDICES_PREFIX = "custom_indices_"
_VERIFY_PREFIX = "custom_indices_verify_"

#: 现有指标行里「指标名称 / 公式 / ID」的候选字段名（响应字段名未实测，逐一探测）。
_NAME_KEYS = ("indexName", "name", "index_name")
_FORMULA_KEYS = ("indexFormula", "formula", "index_formula")
_ID_KEYS = ("id", "indexId", "index_id")

#: 执行结果状态（与 approval.record_changes 约定一致；累计闸只统计 success）。
VALID_STATUSES: Tuple[str, ...] = ("success", "failed", "skipped")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def load_skill_config(path: Optional[str] = None) -> Dict[str, Any]:
    """读取技能配置 config/metric_governance.yaml（含父目录的 _shared 配置引用）。"""
    cfg_path = Path(path) if path else (_SKILL_DIR / "config" / "metric_governance.yaml")
    return cfgmod.load_yaml_file(cfg_path)


def load_thresholds() -> Dict[str, Any]:
    """读取 _shared/config/thresholds.yaml。"""
    loader = cfgmod.ConfigLoader(shared_root=str(SHARED_ROOT))
    return loader.load("thresholds")


def target_report_ids(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """返回 (report_id_list, 未配置 ID 的报表键)。空 id 不计入，并回报缺失键提示回填。

    v1.7.0 起共享 reports.yaml 自动发现缓存已移除：报表 ID 由使用者从赛狐网页端
    确认后维护在本技能 config 的 ``report_ids`` 映射（不猜 ID）。
    """
    ids: List[str] = []
    missing: List[str] = []
    table = cfg.get("report_ids") or {}
    for key in cfg.get("target_reports") or []:
        rid = table.get(key)
        if rid:
            ids.append(str(rid))
        else:
            missing.append(key)
    return ids, missing


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class DesiredIndex:
    """一条待治理指标（来自技能配置 desired_indices）。"""

    name: str
    type: str = "0"
    formula: str = ""
    formula_status: str = "needs_human"
    source_fields: List[str] = dc_field(default_factory=list)
    comment: str = ""

    @property
    def verified(self) -> bool:
        return self.formula_status == "verified" and bool(self.formula)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DesiredIndex":
        return cls(
            name=str(d.get("name", "")).strip(),
            type=str(d.get("type", "0")),
            formula=str(d.get("formula") or ""),
            formula_status=str(d.get("formula_status", "needs_human")),
            source_fields=list(d.get("source_fields") or []),
            comment=str(d.get("comment") or ""),
        )


@dataclass
class RawReport:
    """``out/raw/`` 一组落盘信封的读取结论（多页合并 + 披露信息）。"""

    rows: List[Dict[str, Any]] = dc_field(default_factory=list)
    notes: List[str] = dc_field(default_factory=list)  # 降级/披露说明（必须进输出）
    demo_fingerprint: Optional[str] = None
    truncated: bool = False
    pages_ok: int = 0
    pages_bad: int = 0

    @property
    def reliable(self) -> bool:
        """**取数可信门**：至少一页成功，且无失败/损坏页、无截断。

        写提案的安全前提——现有指标清单必须完整可信，否则「指标在未取到的页上」
        会被误判为缺失，生成错误的新增提案（在赛狐重复建指标）。
        不可信时调用方一律不生成提案，只输出「需人工核查」。
        """
        return self.pages_ok >= 1 and self.pages_bad == 0 and not self.truncated


# ---------------------------------------------------------------------------
# 落盘读取（零网络）
# ---------------------------------------------------------------------------


def _read_envelope(path: Path) -> Tuple[Optional[Any], List[str], Optional[str]]:
    """读取并体检一个落盘信封（解析/状态/空数据/partial/截断）。

    :returns: ``(RawEnvelope 或 None, notes, truncated)``；None = 文件缺失或损坏。
    """
    notes: List[str] = []
    if not path.exists():
        notes.append(f"❌ {path.stem}：缺少落盘文件 {path}")
        return None, notes, False
    try:
        env = load_envelope(path)
    except Exception as exc:  # JSON 损坏 / 非对象
        notes.append(f"❌ {path.stem}：落盘文件无法解析（{exc}）")
        return None, notes, False
    if env.status not in ("complete", "partial"):
        notes.append(
            f"❌ {path.stem}：status={env.status} error_code={env.error_code} {env.message or ''}"
            "（按 workflow.md 降级路径处理，不重试）"
        )
        return env, notes, False
    truncated = False
    if is_no_data(env.data):
        notes.append(f"⬜ {path.stem}：无数据（标「无数据」，不写 0，不重试）")
        return env, notes, truncated
    if env.partial:
        notes.append(f"🟡 {path.stem}：status=partial（读可采信但须在数据说明披露）")
    cov = env.coverage or {}
    if cov.get("truncated"):
        truncated = True
        notes.append(
            f"⚠️ {path.stem}：本次仅覆盖 {cov.get('fetched_count')} 行"
            f"（服务端报告约 {cov.get('total_count')} 行），响应声明截断，其余未纳入比对。"
        )
    if cov.get("complete") is not True and not truncated:
        notes.append(f"⚠️ {path.stem}：覆盖范围未知，未据此认定达到 max_rows 上限。")
    return env, notes, truncated


def load_existing_indices(raw_dir: Path, cfg: Dict[str, Any]) -> RawReport:
    """读取现有自定义指标落盘 ``custom_indices_<page>.json``（多页合并）。

    逐页体检（status/空数据/partial/演示指纹/coverage 截断），合并 ``data.rows``；
    结论含**取数可信门**（见 :attr:`RawReport.reliable`）。
    """
    state = RawReport()
    if not raw_dir.exists():
        state.notes.append(
            f"❌ 落盘目录不存在：{raw_dir}。v3 取数面由 agent 完成：请按 "
            "references/workflow.md 的取数计划调 mcp__Sellfox-MCP__{search,action} "
            "落盘 out/raw/ 后重跑。"
        )
        return state
    files = [
        p for p in sorted(raw_dir.glob(f"{_INDICES_PREFIX}*.json"))
        if not p.name.startswith(_VERIFY_PREFIX)  # 回读落盘不算取数页
    ]
    if not files:
        state.notes.append(
            "❌ 缺少 out/raw/custom_indices_<page>.json（取数计划步骤 3）。"
            "v3 取数面由 agent 完成：按 references/workflow.md 落盘后重跑。"
        )
        return state
    max_rows = int(cfg.get("max_rows", 100) or 100)
    for p in files:
        env, notes, truncated = _read_envelope(p)
        state.notes.extend(notes)
        fp = detect_demo_environment(env.obj) if env is not None else None
        if fp and not state.demo_fingerprint:
            state.demo_fingerprint = fp
        if truncated:
            state.truncated = True
        if (env is None or env.status != "complete"
                or env.coverage.get("complete") is not True
                or not isinstance(env.data, dict) or not isinstance(env.data.get("rows"), list)):
            state.pages_bad += 1
            state.notes.append(f"❌ {p.name}：状态、覆盖范围或列表结构不完整，禁止据此推断指标缺失。")
            continue
        state.pages_ok += 1
        state.rows.extend(r for r in env.rows("rows") if isinstance(r, dict))
    if state.rows and len(state.rows) >= max_rows:
        state.truncated = True
        state.notes.append(
            f"⚠️ 合并 {len(files)} 页共 {len(state.rows)} 行，达 max_rows={max_rows} 安全阀，"
            "其余未纳入比对。"
        )
    return state


def load_official_trees(raw_dir: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], Optional[str]]:
    """读取官方指标目录落盘 ``stat_index_op1/2.json`` 并防御性解析。

    :returns: ``(nodes_by_op, notes, demo_fingerprint)``。文件缺失/失败/解析不到时
              记 notes（「需人工核查」），**不臆造节点**；官方目录只作基线参照，
              缺失不阻断比对（降级为「只比对自定义指标」）。
    """
    trees: Dict[str, List[Dict[str, Any]]] = {}
    notes: List[str] = []
    demo: Optional[str] = None
    for op, step in STAT_STEPS.items():
        env, env_notes, _ = _read_envelope(raw_dir / f"{step}.json")
        notes.extend(env_notes)
        if env is None or env.status != "complete":
            notes.append(f"⚠️ 官方目录(op_type={op}) 不可用：基线缺失，本 run 只比对自定义指标。")
            continue
        fp = detect_demo_environment(env.obj)
        demo = demo or fp
        if is_no_data(env.data):
            trees[op] = []
            continue
        nodes = normalize_tree(env.data)
        if not nodes:
            notes.append(
                f"⚠️ 官方目录(op_type={op}) 结构未能解析（返回形态未实测），需人工核查，不臆造节点。"
            )
        trees[op] = nodes
    return trees, notes, demo


def normalize_tree(data: Any) -> List[Dict[str, Any]]:
    """防御性把官方目录 data 规范成**顶级**节点列表（结构未实测）。

    返回的节点 dict 原样保留 ``children``（嵌套子树不在此展开）；
    全层级遍历/计数/落盘用 :func:`iter_tree_nodes` / :func:`tree_stats` /
    :func:`tree_summary`（children 不再静默丢弃）。
    """
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("rows", "list", "records", "children", "dataList"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # 递归找第一个「元素为 dict 且像指标节点」的 list
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if any(k in value[0] for k in ("mappedIndex", "indexName", "name")):
                    return [x for x in value if isinstance(x, dict)]
    return []


def _pick(row: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def index_name_of(row: Dict[str, Any]) -> Optional[str]:
    return _pick(row, _NAME_KEYS)


def index_formula_of(row: Dict[str, Any]) -> Optional[str]:
    return _pick(row, _FORMULA_KEYS)


def index_id_of(row: Dict[str, Any]) -> Optional[str]:
    return _pick(row, _ID_KEYS)


# ---------------------------------------------------------------------------
# 官方目录树：递归全层级（线上实测为嵌套树，children 多层）
# ---------------------------------------------------------------------------

#: 节点里子树列表的候选字段名（线上实测为 ``children``；防御性兼容其它命名）。
_CHILDREN_KEYS: Tuple[str, ...] = ("children", "childList")


def _node_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """防御性取一个节点的子节点列表（候选键逐一探测；取不到返回空，不臆造）。"""
    for key in _CHILDREN_KEYS:
        value = node.get(key)
        if isinstance(value, list):
            return [c for c in value if isinstance(c, dict)]
    return []


def iter_tree_nodes(nodes: Sequence[Dict[str, Any]], depth: int = 1):
    """递归遍历官方目录树**全部层级**（yield ``(层级, 节点)``，children 不再丢弃）。"""
    for node in nodes:
        yield depth, node
        kids = _node_children(node)
        if kids:
            yield from iter_tree_nodes(kids, depth + 1)


def tree_stats(nodes: Sequence[Dict[str, Any]]) -> Tuple[int, Dict[int, int]]:
    """统计总节点数与各层级节点数。

    :returns: ``(总节点数, {层级: 节点数})``。线上真实目录 = 10 一级 × 3 层 = 30 节点。
    """
    levels: Dict[int, int] = {}
    total = 0
    for depth, _node in iter_tree_nodes(nodes):
        levels[depth] = levels.get(depth, 0) + 1
        total += 1
    return total, levels


def compact_tree(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把官方目录规范成紧凑全量树（id/name/mappedIndex/层级/children 递归）。"""
    out: List[Dict[str, Any]] = []
    for node in nodes:
        kids = _node_children(node)
        out.append(
            {
                "id": node.get("id"),
                "name": _pick(node, ("name", "indexName")),
                "mapped_index": _pick(node, ("mappedIndex", "indexEnName")),
                "level": node.get("level") or str(len(_as_id_list(node.get("parentId"))) + 1),
                "children": compact_tree(kids) if kids else [],
            }
        )
    return out


def _as_id_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def tree_summary(nodes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """官方目录全量摘要：顶级数 / 总节点数 / 分层计数 / 紧凑全量树（供 agent 作答）。"""
    total, levels = tree_stats(nodes)
    return {
        "top_level": len(nodes),
        "total_nodes": total,
        "nodes_by_level": {str(k): v for k, v in sorted(levels.items())},
        "tree": compact_tree(nodes),
    }


# ---------------------------------------------------------------------------
# 差异比对（纯离线）
# ---------------------------------------------------------------------------


def _norm_formula(text: Optional[str]) -> str:
    return "".join(str(text or "").split()).lower()


@dataclass
class Plan:
    adds: List[DesiredIndex] = dc_field(default_factory=list)
    updates: List[Tuple[DesiredIndex, Dict[str, Any], str]] = dc_field(default_factory=list)
    aligned: List[str] = dc_field(default_factory=list)
    pending: List[DesiredIndex] = dc_field(default_factory=list)
    #: pending 中因「现有行缺真实 indexId」而待确认的指标名（其余为公式未人工确认 needs_human）。
    pending_missing_id: List[str] = dc_field(default_factory=list)

    def pending_reason(self, d: DesiredIndex) -> str:
        """区分两种待确认原因：缺真实 ID（待补 indexId）/ 公式未确认（needs_human）。"""
        if d.name in self.pending_missing_id:
            return "missing_id"
        return "needs_human"

    def describe_lines(self) -> List[str]:
        return [
            f"新增 {len(self.adds)} ｜ 更新 {len(self.updates)} ｜ "
            f"已对齐 {len(self.aligned)} ｜ 待确认公式或标识 {len(self.pending)}"
        ]


def build_plan(existing: Sequence[Dict[str, Any]], desired: Sequence[DesiredIndex]) -> Plan:
    """按指标名称 join，产出新增/更新/已对齐/待确认四类。"""
    by_name: Dict[str, Dict[str, Any]] = {}
    for row in existing:
        name = index_name_of(row)
        if name:
            by_name[name] = row

    plan = Plan()
    for d in desired:
        if not d.name:
            continue
        current = by_name.get(d.name)
        if not d.verified:
            plan.pending.append(d)
            continue
        if current is None:
            plan.adds.append(d)
            continue
        old_formula = index_formula_of(current) or ""
        if _norm_formula(old_formula) != _norm_formula(d.formula):
            if index_id_of(current) is None:
                plan.pending.append(d)  # 公式已确认，但现有行缺真实 indexId → 待补 ID，不能用名称充当 ID。
                plan.pending_missing_id.append(d.name)
            else:
                plan.updates.append((d, current, old_formula))
        else:
            plan.aligned.append(d.name)
    return plan


# ---------------------------------------------------------------------------
# 提案（dry-run，纯离线）
# ---------------------------------------------------------------------------


def build_proposal(plan: Plan, skill: str = "metric-governance") -> "ap.Proposal":
    """构造 dry-run 提案（L2）。更新项写前快照旧公式作为回滚依据。

    每项 ``extra.contract_version = None`` **占位**：写契约变更频率高，
    执行前必须由 agent 重取（search detail=true）后填充（SELLFOX_CLIENT.md §2）。
    """
    p = ap.Proposal(
        skill=skill,
        title="指标口径固化提案（dry-run）",
        risk_level="L2",
        dry_run=True,
    )
    for d in plan.adds:
        p.add(
            ap.ChangeItem(
                tool="add_custom_report_index",
                target_type="index",
                target_id=d.name,
                field="index_formula",
                before=None,
                after=d.formula,
                reason=f"缺失：口径白皮书要求；依据字段 {', '.join(d.source_fields) or '—'}",
                action="add_index",
                risk="L2",
                extra={
                    "index_name": d.name,
                    "type": d.type,
                    "index_comment": d.comment,
                    "contract_version": None,  # 占位：执行前重取
                },
            )
        )
    for d, current, old_formula in plan.updates:
        idx_id = index_id_of(current)
        if idx_id is None:
            raise ValueError(f"指标 {d.name} 缺少真实 ID，不能生成更新提案")
        p.add(
            ap.ChangeItem(
                tool="update_custom_report_index",
                target_type="index",
                target_id=idx_id,
                field="index_formula",
                before=old_formula,
                after=d.formula,
                reason="口径不一致：与口径白皮书对齐",
                action="update_index",
                risk="L2",
                extra={
                    "id": idx_id,
                    "index_name": d.name,
                    "type": d.type,
                    "index_comment": d.comment,
                    "contract_version": None,  # 占位：执行前重取
                },
            )
        )
    return p


def split_into_batches(items: Sequence["ap.ChangeItem"], size: int = 20) -> List[List["ap.ChangeItem"]]:
    """按单次上限分批（超限时分别审批）。"""
    size = max(1, int(size))
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def proposal_to_dict(p: "ap.Proposal") -> Dict[str, Any]:
    """提案序列化为可落盘 JSON（--execute 重建序号时以此为准）。"""
    return {
        "skill": p.skill,
        "title": p.title,
        "risk_level": p.risk_level,
        "dry_run": p.dry_run,
        "created_at": p.created_at,
        "items": [dataclasses.asdict(it) for it in p.items],
    }


def gate_check(items: Sequence["ap.ChangeItem"], thresholds: Dict[str, Any], log_path: str) -> List[str]:
    """累计闸检查（读 ad_changes.jsonl）。返回违规说明列表（空=通过）。"""
    gate = ap.AccumulatorGate(log_path, limits=thresholds.get("approval") or {})
    return gate.check(items)


# ---------------------------------------------------------------------------
# 执行清单（--execute 的产物：脚本不写，交给 agent 按批准逐项执行）
# ---------------------------------------------------------------------------

#: 写操作 agent 执行协议（写进执行清单，逐项照做；详见 references/workflow.md）。
AGENT_PROTOCOL: Tuple[str, ...] = (
    "1. 只执行被人工批准的条目（清单 items[].index 命中批准序号）；未批准项一律跳过。",
    "2. 执行前重取写契约：search(tool_ids=[tool_id], detail=true)，取当次 contract_version "
    "填入 params 同级（写契约变更频率高，禁止沿用旧版本/占位 null）。",
    "3. 参数类型/必填/枚举以当次 inputSchema 校正 params_template（未声明字段不传，"
    "additionalProperties 会拒）。",
    "4. 调 action(tool_id, params, contract_version)。",
    "5. 每条执行后无论成败都回写结果并运行 record："
    "run_governance.py --record <results.json> --manifest <本清单>。",
    "6. 全部执行完后重取现有指标落盘 out/raw/custom_indices_verify_<page>.json，"
    "再运行 run_governance.py --verify --manifest <本清单> 做公式回读比对。",
    "7. 失败 / status=partial / 回读不一致：一律按失败处理，不重放、不自动重试，"
    "列「需人工核查」。",
)


def _item_params(item: "ap.ChangeItem") -> Dict[str, Any]:
    """从 ChangeItem 构造写工具参数模板（不含 contract_version；类型以当次契约校正）。"""
    extra = dict(item.extra or {})
    params: Dict[str, Any] = {
        "index_name": str(extra.get("index_name") or item.target_id),
        "index_formula": item.after,
    }
    if extra.get("type") not in (None, ""):
        params["type"] = extra.get("type")
    if str(extra.get("index_comment") or "").strip():
        params["index_comment"] = extra["index_comment"]
    if item.tool == "update_custom_report_index":
        params["id"] = str(extra.get("id") or item.target_id)
    return params


def build_execute_manifest(
    items: Sequence["ap.ChangeItem"],
    *,
    operator: str,
    approval_text: str = "",
    skill: str = "07-metric-governance",
    demo_fingerprint: Optional[str] = None,
    notes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """生成「执行清单」：tool_id × 参数模板 × 旧公式快照。

    ⚠️ 本清单**不是**执行：脚本零网络、永不直接调写工具。agent 按清单与
    :data:`AGENT_PROTOCOL` 逐项执行；``contract_version`` 为 ``null`` 占位，
    执行前必须重取写契约后填充。
    """
    return {
        "skill": skill,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "operator": operator,
        "approval_text": approval_text,
        "mode": "agent-execute",
        "demo_fingerprint": demo_fingerprint,
        "notes": list(notes or []),
        "protocol": list(AGENT_PROTOCOL),
        "items": [
            {
                "index": it.index,
                "tool_id": it.tool,
                "action": it.action,
                "risk": it.risk,
                "target_type": it.target_type,
                "target_id": str(it.target_id),
                "index_name": str((it.extra or {}).get("index_name") or it.target_id),
                "params_template": _item_params(it),
                "contract_version": None,  # ← 占位：执行前重取（SELLFOX_CLIENT.md §2）
                "old_formula_snapshot": it.before,  # 回滚依据（新增为 null）
                "expected_formula": it.after,
                "reason": it.reason,
            }
            for it in items
        ],
    }


def manifest_change_items(manifest: Dict[str, Any]) -> List["ap.ChangeItem"]:
    """把执行清单条目还原为 ChangeItem（供 record_changes / 累计闸使用）。"""
    items: List[ap.ChangeItem] = []
    for m in manifest.get("items") or []:
        params = m.get("params_template") or {}
        items.append(
            ap.ChangeItem(
                tool=str(m.get("tool_id") or ""),
                target_type=str(m.get("target_type") or "index"),
                target_id=str(m.get("target_id") or ""),
                field="index_formula",
                before=m.get("old_formula_snapshot"),
                after=m.get("expected_formula"),
                reason=str(m.get("reason") or ""),
                action=str(m.get("action") or ""),
                risk=str(m.get("risk") or "L2"),
                extra={
                    "index_name": m.get("index_name"),
                    "type": params.get("type"),
                    "index_comment": params.get("index_comment", ""),
                    "id": params.get("id"),
                    "contract_version": m.get("contract_version"),
                },
                index=int(m.get("index") or 0),
            )
        )
    return items


# ---------------------------------------------------------------------------
# record helper：agent 执行结果 → 变更日志
# ---------------------------------------------------------------------------


def record_execution(
    manifest: Dict[str, Any],
    results: Sequence[Dict[str, Any]],
    *,
    operator: str,
    log_path: str,
) -> Dict[str, Any]:
    """把 agent 回传的执行结果追加进变更日志（``ad_changes.jsonl``）。

    :param results: JSON 数组，每条 ``{"index": 1, "status": "success|failed|skipped",
                    "contract_version": "...", "note": "..."}``。
                    实际使用的 contract_version 会写进日志 extra（写契约可审计）。
    :returns: 汇总（recorded/unknown/invalid/log_path）。
    """
    items = manifest_change_items(manifest)
    by_index = {it.index: it for it in items}
    recorded: Dict[str, List[int]] = {"success": [], "failed": [], "skipped": []}
    unknown: List[Any] = []
    invalid: List[Any] = []
    for r in results or []:
        try:
            idx = int(r.get("index"))
        except (TypeError, ValueError):
            invalid.append(r)
            continue
        it = by_index.get(idx)
        if it is None:
            unknown.append(r)
            continue
        status = str(r.get("status") or "")
        if status not in VALID_STATUSES:
            invalid.append(r)
            continue
        cv = r.get("contract_version")
        if cv is not None:
            it.extra["contract_version"] = cv
        if r.get("note"):
            it.extra["note"] = r.get("note")
        ap.record_changes([it], operator=operator, status=status, log_path=log_path)
        recorded[status].append(idx)
    return {
        "recorded": {k: v for k, v in recorded.items() if v},
        "unknown": unknown,
        "invalid": invalid,
        "log_path": log_path,
    }


# ---------------------------------------------------------------------------
# verify helper：写后回读公式比对（零网络）
# ---------------------------------------------------------------------------


def gather_verify_rows(
    raw_dir: Path, verify_files: Optional[Sequence[str]] = None
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """收集写后回读落盘（``custom_indices_verify_<page>.json``）。"""
    notes: List[str] = []
    paths = (
        [Path(f) for f in verify_files]
        if verify_files
        else sorted(raw_dir.glob(f"{_VERIFY_PREFIX}*.json"))
    )
    if not paths:
        notes.append(
            f"❌ 未找到写后回读落盘（{raw_dir / (_VERIFY_PREFIX + '<page>.json')}）。"
            "请 agent 在执行完成后重取 get_custom_report_index_page_list"
            "（share_status=0）落盘再跑 --verify。"
        )
        return [], notes
    rows: List[Dict[str, Any]] = []
    for p in paths:
        env, env_notes, _ = _read_envelope(p)
        notes.extend(env_notes)
        if env is None or env.status != "complete":
            continue
        rows.extend(r for r in env.rows("rows") if isinstance(r, dict))
    return rows, notes


def verify_against_rows(
    manifest: Dict[str, Any], rows: Sequence[Dict[str, Any]], cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """按执行清单逐项做写后回读公式比对（零网络）。

    verdict ∈ ``match`` / ``mismatch`` / ``not_found`` / ``unknown_field``；
    后三类都**不得记 success**（宁可漏报成功，不可虚报成功）。
    """
    formula_keys = tuple(cfg.get("readback_formula_fields") or _FORMULA_KEYS)
    id_keys = tuple(cfg.get("readback_id_fields") or _ID_KEYS)
    by_name: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        n = index_name_of(r)
        if n:
            by_name.setdefault(n, r)

    results: List[Dict[str, Any]] = []
    for m in manifest.get("items") or []:
        name = str(m.get("index_name") or m.get("target_id"))
        row = by_name.get(name)
        if row is None:
            for r in rows:
                if str(_pick(r, id_keys) or "") == str(m.get("target_id")):
                    row = r
                    break
        base = {
            "index": m.get("index"),
            "tool_id": m.get("tool_id"),
            "index_name": name,
            "expected": m.get("expected_formula"),
        }
        if row is None:
            results.append(
                dict(base, verdict="not_found", actual=None,
                     verdict_text="回读未找到该指标（可能分页未覆盖）→ 无法判定，需人工核查，不得记 success")
            )
            continue
        actual = _pick(row, formula_keys)
        if actual is None:
            results.append(
                dict(base, verdict="unknown_field", actual=None,
                     verdict_text="回读字段名未知（响应字段名未经实测）→ 需人工核查，不得记 success")
            )
            continue
        ok = _norm_formula(actual) == _norm_formula(m.get("expected_formula"))
        results.append(
            dict(
                base,
                verdict="match" if ok else "mismatch",
                actual=actual,
                verdict_text="一致" if ok else "不一致（不重放；核对公式后列「需人工核查」，不得记 success）",
            )
        )
    return results


# ---------------------------------------------------------------------------
# verify 补偿记录：record 先于 verify 的顺序张力（mismatch 时追加 amend，不改写历史）
# ---------------------------------------------------------------------------


def _read_change_log(log_path: str) -> List[Dict[str, Any]]:
    """读取变更日志全部记录（文件缺失/损坏行跳过，不抛错）。"""
    path = Path(log_path)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def append_verify_amends(
    manifest: Dict[str, Any],
    results: Sequence[Dict[str, Any]],
    *,
    operator: str,
    log_path: str,
) -> List[Dict[str, Any]]:
    """verify 检出 ``mismatch`` 且原记录为 success 时，向变更日志**追加**补偿记录。

    背景（record-then-verify 顺序张力）：协议要求每条执行后即 record（可能已落
    ``success``），若 action 被 isError=false 误报成功而回读实为 mismatch，原记录
    无法撤回。本函数不改写历史（原 success 保持原值），而是**追加**一条
    ``type=amend`` 的独立记录指向原记录，并带 ``status_note="verify mismatch"``。

    累计闸（``_shared approval.AccumulatorGate``）只统计 ``status=success``：
    amend 记录 status 置 ``"amend"`` 不再额外计数；**原始 success 仍会被计数**，
    该风险由 status_note 披露（verify 输出同步说明）。
    """
    bad = [r for r in results if r.get("verdict") == "mismatch"]
    if not bad:
        return []
    items = {int(m.get("index") or 0): m for m in manifest.get("items") or []}
    last_by_key: Dict[Any, Dict[str, Any]] = {}
    for rec in _read_change_log(log_path):
        last_by_key[(rec.get("index"), str(rec.get("target_id") or ""))] = rec
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    amends: List[Dict[str, Any]] = []
    for r in bad:
        m = items.get(int(r.get("index") or 0))
        if m is None:
            continue
        orig = last_by_key.get((m.get("index"), str(m.get("target_id") or "")))
        if not orig or orig.get("status") != "success":
            continue  # 原记录非 success（本就按失败处理）→ 无「误报成功」需要补偿。
        amends.append(
            {
                "type": "amend",
                "tool": m.get("tool_id"),
                "target_type": str(m.get("target_type") or "index"),
                "target_id": str(m.get("target_id") or ""),
                "field": "index_formula",
                "before": m.get("expected_formula"),  # 期望（原记录 after）
                "after": r.get("actual"),             # 回读实值
                "reason": "verify mismatch 补偿：回读与期望不一致，原 success 记录存疑"
                          "（不重放、需人工核查）",
                "action": "verify_amend",
                "risk": str(m.get("risk") or "L2"),
                "extra": {
                    "index_name": m.get("index_name"),
                    "amend_for": {
                        "index": m.get("index"),
                        "target_id": str(m.get("target_id") or ""),
                        "original_status": orig.get("status"),
                        "original_ts": orig.get("ts"),
                    },
                    "status_note": "verify mismatch",
                },
                "index": m.get("index"),
                "operator": operator,
                "status": "amend",  # 非 success：不被累计闸计数；原始 success 计数风险见 status_note
                "status_note": "verify mismatch（补偿记录：不改写历史；累计闸仍按原始 success 计数）",
                "ts": ts,
            }
        )
    if amends:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for rec in amends:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return amends


# ---------------------------------------------------------------------------
# JSON 落盘小工具
# ---------------------------------------------------------------------------


def save_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - 离线自检（零网络）
    print(f"shared_root = {SHARED_ROOT}")
    cfg = load_skill_config()
    print(f"dry_run_default = {cfg.get('dry_run_default')} ｜ max_rows = {cfg.get('max_rows')}")
    ids, missing = target_report_ids(cfg)
    print(f"report_id_list = {ids} ｜ 缺失报表键 = {missing}")
    desired = [DesiredIndex.from_dict(d) for d in (cfg.get("desired_indices") or [])]
    demo_existing = [{"indexName": "客单价-公司口径", "indexFormula": "productSales/orderNum", "id": "1"}]
    plan = build_plan(demo_existing, desired)
    print("差异比对（示例）：", "；".join(plan.describe_lines()))
    proposal = build_proposal(plan)
    print(f"提案条数 = {len(proposal.items)}（全部 needs_human 时为 0，属正常）")

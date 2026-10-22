"""人机审核审批流封装 —— _shared 共享底座。

落地《12-Skill方案-最终版.md》第三章「人机审核统一规范」，**所有写操作走同一条通道**：

    ① 诊断（只读） → ② 提案（dry-run 对比表） → ③ 会话内人工确认
    → ④ 执行（写） → ⑤ 记录 + 可回滚

本模块提供：
  * 风险分级常量 :data:`RISK_LEVELS`（L1~L4）与默认行为。
  * :class:`Proposal` / :class:`ChangeItem` —— 结构化提案，含变更前后对比与回滚方案。
  * 强制约束校验 :meth:`Proposal.validate` —— 单次 ≤20 条、预算幅度 ≤±30%、禁止清单。
  * :class:`AccumulatorGate` —— 累计闸：防连续调用绕过单次限额（读 ``ad_changes.jsonl``）。
  * 确认解析 :func:`parse_confirmation`（v1.7.0 起审批为会话内人工确认，飞书能力已移除）、
    变更日志 :func:`record_changes`、写后回读 :func:`verify_read_back`。

仅依赖标准库（``json`` / ``os`` / ``time`` / ``dataclasses``）。

示例::

    from approval import ChangeItem, Proposal, parse_confirmation, record_changes

    p = Proposal(skill="ad-optimizer", title="广告优化提案")
    p.add(ChangeItem(tool="edit_sp_campaign", target_type="campaign",
                     target_id="C001", field="budget", before=100, after=120,
                     reason="ACOS 低于目标且曝光不足"))
    p.validate()                      # 违反强制约束会抛 ApprovalError
    print(p.render_markdown())        # dry-run 对比表
"""

from __future__ import annotations

import json
import os
import time
import dataclasses
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 变更日志文件名（默认落在当前工作目录）。
DEFAULT_LOG_PATH = "ad_changes.jsonl"

#: 写操作风险分级与默认行为（来自方案第三章）。
RISK_LEVELS: Dict[str, Dict[str, Any]] = {
    "L1": {
        "name": "低风险",
        "desc": "只读性质的写（如 create_ad_download_task 下载报表）",
        "behavior": "直接执行",
    },
    "L2": {
        "name": "中风险",
        "desc": "改预算/状态/竞价、加投放、自定义指标（add/update_custom_report_index）",
        "behavior": "dry-run 预览 → 逐项确认 → 执行；单次 ≤20 条，幅度 ≤±30%",
    },
    "L3": {
        "name": "高风险",
        "desc": "批量暂停全部活动、预算清零、删除投放",
        "behavior": "默认禁止（禁止清单）；需显式开启 + 二次确认",
    },
    "L4": {
        "name": "极高风险",
        "desc": "改 Listing、改价格",
        "behavior": "技能不做（MCP 也无此能力）",
    },
}

#: 禁止清单（命中即拒绝）。
FORBIDDEN_ACTIONS: Sequence[str] = ("batch_pause_all", "zero_budget", "delete_targeting")

#: approval 闸门默认值（应被 config/thresholds.yaml 的 approval 段覆盖）。
DEFAULT_LIMITS: Dict[str, Any] = {
    "max_items_per_batch": 20,
    "max_budget_change_pct": 30.0,
    "max_changes_per_campaign_24h": 2,
    "max_net_change_pct_24h": 30.0,
    # 净变化累计的回看窗口（小时）：默认 7 天，防止跨天/跨周拆批复利绕过 ±上限
    "net_change_lookback_hours": 168,
    "max_daily_budget_increment": 5000.0,
    "require_read_back": True,
    "forbid_batch_pause_all": True,
    "forbid_zero_budget": True,
    "forbid_delete_targeting": True,
}


class ApprovalError(RuntimeError):
    """提案违反强制约束或审批流程错误。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ChangeItem:
    """单条变更项（一行 = 一个对象的某个字段的前后变化）。

    :param tool: 执行用的 MCP 工具名，如 ``edit_sp_campaign``。
    :param target_type: 对象类型：``campaign`` / ``targeting`` / ``index`` / ``keyword``。
    :param target_id: 对象 ID（campaignId / keywordId / index id ...）。
    :param field: 被改字段，如 ``budget`` / ``state`` / ``bid``。
    :param before: 变更前的值（写前快照，用于回滚）。
    :param after: 变更后的值。
    :param reason: 变更理由（写进提案，供审批人判断）。
    :param action: 语义化动作标签（用于禁止清单匹配），如 ``set_budget``。
    :param risk: 风险级别：L1~L4。
    :param extra: 工具所需的其它参数（如 shop_id）。
    """

    tool: str
    target_type: str
    target_id: str
    field: str
    before: Any
    after: Any
    reason: str = ""
    action: str = ""
    risk: str = "L2"
    # 注意：本类有名为 field 的属性，故显式用 dataclasses.field 避免与注解同名歧义
    extra: Dict[str, Any] = dataclasses.field(default_factory=dict)
    index: int = 0  # 提案内序号（1-based），render/confirm 用

    @property
    def delta_pct(self) -> Optional[float]:
        """数值型字段的相对变化百分比；非数值返回 ``None``。"""
        try:
            b = float(self.before)
            a = float(self.after)
        except (TypeError, ValueError):
            return None
        if b == 0:
            return None
        return (a - b) / b * 100.0

    def to_row(self) -> str:
        """渲染为提案表格的一行。"""
        delta = self.delta_pct
        delta_txt = f"{delta:+.1f}%" if delta is not None else "—"
        return (
            f"| {self.index} | {self.tool} | {self.target_id} | {self.field} "
            f"| {self.before} → {self.after} | {delta_txt} | {self.reason or '—'} |"
        )

    def to_log_dict(self, operator: str, status: str) -> Dict[str, Any]:
        """转换为变更日志记录（时间 / 对象 / 变更前 / 变更后 / 操作人）。"""
        d = asdict(self)
        d.update({"operator": operator, "status": status, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return d


@dataclass
class Proposal:
    """一个 dry-run 变更提案。

    :param skill: 产出提案的技能名（如 ``ad-optimizer``）。
    :param title: 提案标题。
    :param items: 变更项列表。
    :param risk_level: 提案整体风险级别。
    :param dry_run: 是否为演练（默认 ``True``，不可跳过）。
    """

    skill: str
    title: str
    items: List[ChangeItem] = dataclasses.field(default_factory=list)
    risk_level: str = "L2"
    dry_run: bool = True
    created_at: str = dataclasses.field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    def add(self, item: ChangeItem) -> "ChangeItem":
        """追加一条变更项，并分配序号。"""
        item.index = len(self.items) + 1
        self.items.append(item)
        return item

    # -- 约束校验 ---------------------------------------------------------

    def validate(self, limits: Optional[Dict[str, Any]] = None) -> None:
        """校验强制约束，违反即抛 :class:`ApprovalError`。

        校验项：
          1. 单次变更 ≤ ``max_items_per_batch``（默认 20）。
          2. 数值型字段幅度 ≤ ``max_budget_change_pct``（默认 ±30%）。
          3. 禁止清单：批量全暂停 / 预算清零 / 删除投放。
          4. L3/L4 默认禁止（需显式开启，本方法一律拒绝）。
        """
        lim = dict(DEFAULT_LIMITS)
        if limits:
            lim.update(limits)

        if len(self.items) > lim["max_items_per_batch"]:
            raise ApprovalError(
                f"单次变更 {len(self.items)} 条，超过上限 {lim['max_items_per_batch']} 条。"
            )

        max_pct = float(lim["max_budget_change_pct"])
        for it in self.items:
            if it.risk in ("L3", "L4"):
                raise ApprovalError(
                    f"[#{it.index}] 风险级别 {it.risk} 默认禁止执行（{it.target_id}/{it.field}）。"
                )
            if it.action in FORBIDDEN_ACTIONS:
                raise ApprovalError(f"[#{it.index}] 命中禁止清单：{it.action}。")

            pct = it.delta_pct
            if pct is not None and abs(pct) > max_pct:
                raise ApprovalError(
                    f"[#{it.index}] {it.target_id}/{it.field} 调整幅度 {pct:+.1f}% "
                    f"超过上限 ±{max_pct:.0f}%。"
                )
            # 预算清零
            if lim.get("forbid_zero_budget") and it.field in ("budget", "bid"):
                try:
                    if float(it.after) == 0:
                        raise ApprovalError(f"[#{it.index}] 禁止预算/竞价清零。")
                except (TypeError, ValueError):
                    pass

        # 批量全暂停
        if lim.get("forbid_batch_pause_all") and len(self.items) >= 2:
            pausing = [i for i in self.items if i.field == "state" and i.after == "paused"]
            if len(pausing) == len(self.items) and len(pausing) >= 2:
                raise ApprovalError("禁止批量全暂停活动。")

    # -- 渲染 -------------------------------------------------------------

    def render_markdown(
        self,
        limits: Optional[Dict[str, Any]] = None,
        *,
        demo_fingerprint: Optional[str] = None,
    ) -> str:
        """渲染为「变更前后对比表」Markdown（dry-run 预览 / 提案文档正文）。

        :param demo_fingerprint: 演示/样本数据指纹——人工审批所见的提案文档**首行**
            必须带水印（eval3-2026-10-22 N1）：提案是审批的依据文档，正文零水印会
            让演示环境的提案被误当真实变更。HTML 链（report_html）把首行 blockquote
            渲染为警示条。
        """
        lim = dict(DEFAULT_LIMITS)
        if limits:
            lim.update(limits)
        risk = RISK_LEVELS.get(self.risk_level, {})
        head: List[str] = []
        if demo_fingerprint:
            head = [
                f"> ⚠️ 演示/样本数据指纹（{demo_fingerprint}）：本提案基于演示数据，"
                "仅供流程演练，不作为真实变更依据。",
                "",
            ]
        lines = [
            *head,
            f"# {self.title}",
            "",
            f"- 技能：`{self.skill}` ｜ 风险级别：**{self.risk_level} {risk.get('name', '')}**",
            f"- 生成时间：{self.created_at} ｜ 变更条数：{len(self.items)}"
            f"（上限 {lim['max_items_per_batch']}，幅度上限 ±{lim['max_budget_change_pct']:.0f}%）",
            f"- 模式：**dry-run（未执行）**" if self.dry_run else "- 模式：执行",
            "",
            "## 变更前后对比",
            "",
            "| # | 工具 | 对象 | 字段 | 变更前 → 变更后 | 幅度 | 理由 |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += [it.to_row() for it in self.items]
        # 回滚文案按「新增项 / 更新项」分野（eval3-2026-10-22 既往发现）：
        # 新增项 before=None、无旧值快照，且 MCP 无删除工具——「改回变更前的值」失真。
        rollback_texts: List[str] = []
        if any(it.before is None for it in self.items):
            rollback_texts.append(
                "新增项（「变更前」为空）无自动回滚——工具无删除能力，需人工在网页端移除。"
            )
            if any(it.before is not None for it in self.items):
                rollback_texts.append(
                    "其余更新项已写前快照旧值（见「变更前」列），"
                    "如需回滚，按上表逐项将字段改回「变更前」的值即可。"
                )
        else:
            rollback_texts.append(
                "所有变更均已在写前快照旧值（见「变更前」列）。"
                "如需回滚，按上表逐项将字段改回「变更前」的值即可。"
            )
        lines += [
            "",
            "## 回滚方案",
            "",
            *rollback_texts,
            "",
            "## 确认方式",
            "",
            "回复「批准 1,3,5」执行指定项；回复「批准全部」执行全部；回复「驳回」则不执行。",
        ]
        return "\n".join(lines)

    def approved_subset(self, approved: Sequence[int]) -> List[ChangeItem]:
        """按批复的序号返回待执行子集。"""
        wanted = set(approved)
        return [it for it in self.items if it.index in wanted]


# ---------------------------------------------------------------------------
# 累计闸：防连续调用绕过单次限额
# ---------------------------------------------------------------------------


class AccumulatorGate:
    """累计闸。读取变更日志，拦住「单次合规、累计越界」的连续调用。

    闸门（方案 3.3b ②）：
      * 单 campaign 24h 内调整次数 ≤ ``max_changes_per_campaign_24h``
      * 单 campaign 24h 净变化 ≤ ``max_net_change_pct_24h``（防 ±30% 复利累加）
      * 全局单日预算增量总额 ≤ ``max_daily_budget_increment``
      * 同一对象同一天不重复改（去重）

    :param log_path: 变更日志路径（``ad_changes.jsonl``）。
    :param limits: 覆盖默认闸门值（一般来自 ``config/thresholds.yaml`` 的 ``approval`` 段）。
    """

    def __init__(self, log_path: str = DEFAULT_LOG_PATH, limits: Optional[Dict[str, Any]] = None) -> None:
        self.log_path = log_path
        self.limits = dict(DEFAULT_LIMITS)
        if limits:
            self.limits.update(limits)

    def _load_since(self, hours: int = 24) -> List[Dict[str, Any]]:
        """读取最近 ``hours`` 小时内的变更记录。"""
        if not os.path.exists(self.log_path):
            return []
        cutoff = time.time() - hours * 3600
        out: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("status") != "success":
                    continue
                try:
                    ts = time.mktime(time.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%S"))
                except (KeyError, ValueError):
                    continue
                if ts >= cutoff:
                    out.append(rec)
        return out

    def check(self, items: Sequence[ChangeItem], today: Optional[str] = None) -> List[str]:
        """检查提案是否越过累计闸，返回**违规说明列表**（空 = 通过）。

        :param items: 待执行的变更项。
        :param today: 判断「同一天」用的日期串（默认今天 ``%Y-%m-%d``）。
        """
        today = today or time.strftime("%Y-%m-%d")
        history = self._load_since(24)
        # 净变化累计用更长回看窗（默认 7 天）：跨天分批的小步调整同样受限幅约束
        lookback_hours = int(self.limits.get("net_change_lookback_hours", 168))
        history_net = self._load_since(lookback_hours) if lookback_hours > 24 else history
        violations: List[str] = []

        per_target: Dict[str, List[Dict[str, Any]]] = {}
        for rec in history:
            per_target.setdefault(str(rec.get("target_id")), []).append(rec)
        per_target_net: Dict[str, List[Dict[str, Any]]] = {}
        for rec in history_net:
            per_target_net.setdefault(str(rec.get("target_id")), []).append(rec)

        daily_budget_increment = 0.0
        for rec in history:
            if rec.get("field") == "budget":
                try:
                    daily_budget_increment += float(rec.get("after", 0)) - float(rec.get("before", 0))
                except (TypeError, ValueError):
                    pass

        for it in items:
            hist = per_target.get(str(it.target_id), [])
            if len(hist) >= self.limits["max_changes_per_campaign_24h"]:
                violations.append(
                    f"#{it.index} {it.target_id} 24h 内已调整 {len(hist)} 次，"
                    f"达上限 {self.limits['max_changes_per_campaign_24h']}。"
                )
            if any(rec.get("ts", "").startswith(today) for rec in hist):
                violations.append(f"#{it.index} {it.target_id} 今日已变更过，去重拦截。")

            # 净变化：回看窗内历史首值的 before 到本次 after（防跨天拆批复利）
            hist_net = per_target_net.get(str(it.target_id)) or []
            if hist_net and it.delta_pct is not None:
                try:
                    base = float(hist_net[0].get("before"))
                    net = (float(it.after) - base) / base * 100.0 if base else 0.0
                    if abs(net) > self.limits["max_net_change_pct_24h"]:
                        span = (f"{lookback_hours // 24} 天" if lookback_hours % 24 == 0
                                else f"{lookback_hours} 小时")
                        violations.append(
                            f"#{it.index} {it.target_id} 回看{span}累计净变化 {net:+.1f}%，"
                            f"超过上限 ±{self.limits['max_net_change_pct_24h']:.0f}%。"
                        )
                except (TypeError, ValueError):
                    pass

            if it.field == "budget":
                try:
                    daily_budget_increment += float(it.after) - float(it.before)
                except (TypeError, ValueError):
                    pass

        if daily_budget_increment > self.limits["max_daily_budget_increment"]:
            violations.append(
                f"单日预算增量总额 {daily_budget_increment:.2f} 超过上限 "
                f"{self.limits['max_daily_budget_increment']:.2f}。"
            )
        return violations


# ---------------------------------------------------------------------------
# 确认解析 / 日志 / 回读
# ---------------------------------------------------------------------------





def parse_confirmation(text: str, total: Optional[int] = None) -> Tuple[str, List[int]]:
    """解析人工确认文本。

    支持：``批准 1,3,5`` / ``1,3,5 批准`` / ``批准全部`` / ``全部批准`` / ``驳回`` /
    ``✅ 1,2`` / ``❌``。

    :param total: 提案总条数（用于「批准全部」展开；缺省则返回空列表表示全部）。
    :returns: ``(decision, approved_indexes)``，decision ∈
              ``{"approve", "approve_all", "reject", "unclear"}``。
    """
    if not text:
        return "unclear", []
    s = text.strip()
    if s in ("驳回", "拒绝", "❌", "不批准", "否"):
        return "reject", []
    if "全部批准" in s or "批准全部" in s or s in ("同意", "✅", "ok", "OK", "Yes", "yes"):
        return "approve_all", list(range(1, (total or 0) + 1)) if total else []
    if "驳回" in s or "不批准" in s:
        return "reject", []

    import re

    nums = [int(n) for n in re.findall(r"\d+", s)]
    if nums and ("批准" in s or "通过" in s or "同意" in s or "✅" in s or s.strip() == ",".join(
        str(n) for n in nums
    )):
        return "approve", sorted(set(nums))
    if nums:
        # 只回了序号，也视为批准（快捷确认交互）。
        return "approve", sorted(set(nums))
    return "unclear", []


def record_changes(
    items: Sequence[ChangeItem],
    *,
    operator: str,
    status: str,
    log_path: str = DEFAULT_LOG_PATH,
) -> str:
    """把变更写入 ``ad_changes.jsonl``（每行一条 JSON，便于累计闸读取）。

    :param status: ``success`` / ``failed`` / ``skipped``。
    :returns: 写入的日志路径。
    """
    with open(log_path, "a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it.to_log_dict(operator, status), ensure_ascii=False) + "\n")
    return log_path


def verify_read_back(
    client: Any,
    read_tool: str,
    read_args: Dict[str, Any],
    *,
    list_key: str = "itemList",
    id_field: str,
    id_value: Any,
    field_name: str,
    expected: Any,
) -> Tuple[bool, Any]:
    """写后 read-back 校验（方案 3.3b ①，最关键的安全机制）。

    MCP 的 ``isError`` 不可靠（参数错误时返回 ``false``），写操作可能「静默失败」。
    因此写后必须同 session 回读目标对象，比对目标值。

    :returns: ``(ok, actual)``。``ok=False`` 时应告警且不更新本地状态。
    """
    body = client.call(read_tool, read_args) or {}
    rows = body.get(list_key) if isinstance(body, dict) else None
    if rows is None and isinstance(body, dict):
        # 兜底尝试 rows
        rows = body.get("rows") or []
    actual = None
    for row in rows or []:
        if str(row.get(id_field)) == str(id_value):
            actual = row.get(field_name)
            break
    ok = _loose_equal(actual, expected)
    return ok, actual


def _loose_equal(a: Any, b: Any) -> bool:
    """宽松比较（容忍字符串/数值差异，如 ``"100"`` vs ``100``、``100.0`` vs ``100``）。"""
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":  # pragma: no cover
    p = Proposal(skill="ad-optimizer", title="示例提案")
    p.add(ChangeItem(tool="edit_sp_campaign", target_type="campaign", target_id="C001",
                     field="budget", before=100, after=120, reason="ACOS 低、曝光不足"))
    p.add(ChangeItem(tool="create_sp_negative_keyword_targeting", target_type="keyword",
                     target_id="KW002", field="add", before=None, after="free shipping",
                     reason="高花费零转化"))
    p.validate()
    print(p.render_markdown())
    print("确认解析：", parse_confirmation("批准 1,3", total=5))
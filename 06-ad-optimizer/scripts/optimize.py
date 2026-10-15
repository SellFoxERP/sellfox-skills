#!/usr/bin/env python3
"""广告优化编排器 —— v3：脚本零网络，写操作由 agent 执行。

五段式不变：诊断 → dry-run 提案 → 审批 → 执行 → 记录/回滚；变的是**执行者**：

子命令：
    diagnose   读 out/raw/ 落盘（agent 已按取数计划取好）→ 诊断 → dry-run 提案
               out/ad_proposal_<start>_<end>.{md,json}（提案 JSON 每项含
               ``tool_id`` 与 ``contract_version: null`` 占位）
    execute    **不调用任何写工具**：解析批准文本 → 累计闸 → 输出「执行清单」
               （被批准项 × tool_id × 参数模板 × before 快照 × read-back 计划 ×
               record 模板），指导 agent 按 workflow.md「写操作 agent 执行协议」
               逐项调 mcp__Sellfox-MCP__action
    record     agent 每执行完一项，把结果（含 read-back 实际值、status）落
               logs/ad_changes.jsonl：``optimize.py record --change change.json``
    verify     对比写后回读与期望，输出差异（read-back 校验的确定性部分）：
               ``optimize.py verify --proposal x.json --actual y.json``
    selftest   用内置合成数据离线自检（不调用 MCP，全绿）

铁律：脚本零 HTTP；写操作只经 agent 按审批流程调 action；写契约**执行前重取**
（contract_version 占位必须替换为当次 search 结果）；写后 read-back；
累计闸防连续调用；演示环境默认阻断写操作。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _bootstrap as boot
import ad_fetch
import diagnose as diag

from _bootstrap import ad_changes_path, load_optimizer, load_thresholds
from approval import AccumulatorGate, ChangeItem, Proposal, parse_confirmation

_ITEM_FIELDS = ("tool", "target_type", "target_id", "field", "before", "after",
                "reason", "action", "risk", "extra", "index")

#: 提案/执行清单中 contract_version 的占位值：执行前必须重新 search 取当次版本。
CONTRACT_VERSION_PLACEHOLDER = None

_WRITE_PROTOCOL_NOTE = (
    "contract_version 为 null 占位：写契约变更频率高于读契约，"
    "每项执行前必须重新 mcp__Sellfox-MCP__search 该 tool_id 取当次 contract_version，"
    "不得沿用旧版本、不得沿用其他项的版本。"
)


# ---------------------------------------------------------------------------
# 报告渲染（模板见 references/output-templates.md）
# ---------------------------------------------------------------------------


def render_report(
    ad_data: Any,
    findings: Sequence[diag.Finding],
    proposal: Optional[Proposal],
    sources_md: str,
    *,
    start_date: str,
    end_date: str,
) -> str:
    lines: List[str] = []
    if ad_data.demo_fingerprint:
        lines += [f"> ⚠️ **演示数据，非真实经营数据**（指纹：{ad_data.demo_fingerprint}）", ""]

    lines += [
        f"# 广告诊断与优化提案 | {start_date} ~ {end_date}",
        "",
        f"- 店铺：`{ad_data.shop_id}` ｜ 绩效来源：`{ad_data.perf_source}`（scope={ad_data.perf_scope}）",
        "",
        "## 一、核心结论",
        "",
    ]
    p0 = [f for f in findings if f.level == "P0"]
    p1 = [f for f in findings if f.level == "P1"]
    lines.append(f"- P0（紧急）：{len(p0)} 项 ｜ P1（建议）：{len(p1)} 项 ｜ "
                 f"可自动执行提案：{len(proposal.items) if proposal else 0} 条")
    lines.append("")

    lines += ["## 二、诊断明细", "", "| 级别 | 规则 | 对象 | 指标 | 结论/动作 |", "|---|---|---|---|---|"]
    for f in findings:
        mv = f.metrics
        metric_txt = " ".join(
            f"{k}={mv[k]:.2f}" for k in ("cost", "acos", "roas", "orders", "clicks", "impressions")
            if mv.get(k) is not None
        ) or "无数据"
        action = f"自动执行：{f.proposed_field}={f.after}" if f.auto_executable else f"人工核查：{f.manual_note or f.reason}"
        lines.append(f"| {f.level} | {f.rule} | {f.target_name}({f.target_id}) | {metric_txt} | {action} |")
    lines.append("")

    lines += ["## 三、dry-run 变更提案（未执行）", ""]
    lines.append(proposal.render_markdown() if proposal else "无可自动执行的提案（详见上表人工核查项）。")
    lines.append("")

    lines += [
        "## 四、数据说明（必读）",
        "",
        ad_data.disclose(),
        "",
        "**参数来源**",
        "",
        sources_md,
        "",
    ]
    for w in ad_data.warnings:
        lines.append(f"- ⚠️ {w}")
    lines += [
        "",
        "## 五、本技能做不到什么",
        "",
        "- 不做素材替换、不做 Listing 修改、不改价格（MCP 亦无此能力）。",
        "- 无法获取账户健康 / 绩效通知 / 政策合规，相关判断一律标注「需人工核查」。",
        "- `servingStatus` 的 `BUDGET_CAPPED` 取值未经实测，本技能不以其自动判定预算受限。",
        "- 关键词级竞价（`edit_sp_targeting` 的 `targetId` 语义）未实测确认，默认不自动执行。",
        "- 本脚本不直接执行任何写操作：执行由 agent 按审批协议逐项调 action（见 workflow.md）。",
    ]
    return "\n".join(lines)


def _sources_markdown() -> str:
    th = load_thresholds()
    ad = th.get("ad") or {}
    return "\n".join([
        "| 参数 | 取值 | 来源 |",
        "|---|---|---|",
        f"| acos_abs_max | {ad.get('acos_abs_max')} | 配置命中（_shared/config/thresholds.yaml） |",
        f"| acos_target | {ad.get('acos_target')} | 配置命中 |",
        f"| roas_min | {ad.get('roas_min')} | 配置命中 |",
        f"| zero_order_cost_min | {ad.get('zero_order_cost_min')} | 配置命中 |",
    ])


# ---------------------------------------------------------------------------
# 提案存取（v3：每项带 tool_id + contract_version 占位）
# ---------------------------------------------------------------------------


def _item_payload(it: ChangeItem) -> Dict[str, Any]:
    """ChangeItem → 提案 JSON 行（含 tool_id 与 contract_version 占位）。"""
    d = asdict(it)
    d["tool_id"] = it.tool  # 与 tool 同值；显式写出便于 agent 直接寻址 action
    d["contract_version"] = CONTRACT_VERSION_PLACEHOLDER
    return d


def save_proposal(path: Path, proposal: Proposal, *, shop_id: str, start: str, end: str,
                  demo_fingerprint: Optional[str]) -> None:
    payload = {
        "meta": {
            "skill": proposal.skill, "title": proposal.title, "risk_level": proposal.risk_level,
            "created_at": proposal.created_at, "shop_id": shop_id,
            "start_date": start, "end_date": end, "demo_fingerprint": demo_fingerprint,
            "note": _WRITE_PROTOCOL_NOTE,
        },
        "items": [_item_payload(it) for it in proposal.items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_proposal(path: Path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = [ChangeItem(**{k: it.get(k) for k in _ITEM_FIELDS}) for it in payload.get("items", [])]
    meta = payload.get("meta") or {}
    proposal = Proposal(
        skill=meta.get("skill", "ad-optimizer"),
        title=meta.get("title", "广告优化提案"),
        items=items,
        risk_level=meta.get("risk_level", "L2"),
    )
    return {"meta": meta, "proposal": proposal}


# ---------------------------------------------------------------------------
# 写操作参数模板 & read-back 计划（纯数据构造，不发请求）
# ---------------------------------------------------------------------------


def _write_args(item: ChangeItem, shop_id: str) -> Dict[str, Any]:
    """把 ChangeItem 映射为对应写工具的参数模板（agent 调 action 时用）。"""
    if item.tool == "edit_sp_campaign":
        entry: Dict[str, Any] = {"campaignId": item.target_id}
        entry[item.field] = item.after
        return {"shop_id": shop_id, "items": [entry]}
    if item.tool == "edit_sp_targeting":
        entry = {
            "targetId": item.target_id,
            "targetingType": (item.extra or {}).get("targetingType", "keyword"),
        }
        entry[item.field] = item.after
        return {"shop_id": shop_id, "items": [entry]}
    if item.tool == "create_sp_negative_keyword_targeting":
        extra = item.extra or {}
        return {"shop_id": shop_id, "items": [{
            "campaignId": extra.get("campaignId"),
            "adGroupId": extra.get("adGroupId"),
            "keywordText": item.after,
            "matchType": "exact",
        }]}
    raise ValueError(f"未支持的写工具：{item.tool}")


def _read_back_plan(item: ChangeItem, shop_id: str) -> Dict[str, Any]:
    """写后 read-back 的确定性计划：读哪个工具、怎么匹配、期望什么（不发请求）。

    agent 拿到本计划后调 action 写入 → 按计划回读 → 把实际值交给
    ``optimize.py record / verify``。
    """
    if item.tool == "edit_sp_campaign":
        return {
            "tool_id": "get_sp_campaign_list",
            "params": {"shop_id": shop_id, "campaign_id": item.target_id},
            "list_key": "itemList", "id_field": "campaignId", "id_value": item.target_id,
            "field": item.field, "expected": item.after,
        }
    if item.tool == "edit_sp_targeting":
        camp = (item.extra or {}).get("campaignId")
        args: Dict[str, Any] = {"shop_id": shop_id}
        if camp:
            args["campaign_id"] = camp
        return {
            "tool_id": "get_sp_keyword_list", "params": args,
            "list_key": "itemList", "id_field": "keywordId", "id_value": item.target_id,
            "field": item.field, "expected": item.after,
        }
    if item.tool == "create_sp_negative_keyword_targeting":
        camp = (item.extra or {}).get("campaignId")
        args = {"shop_id": shop_id}
        if camp:
            args["campaign_id"] = camp
        return {
            "tool_id": "get_sp_negative_keyword_list", "params": args,
            "list_key": "itemList", "id_field": "keywordText", "id_value": item.after,
            "field": "keywordText", "expected": item.after,
            "note": "新增否定词按 keywordText 集合包含校验（无稳定 ID 可回读）。",
        }
    return {"tool_id": "", "note": f"未内置 {item.tool} 的回读计划，请人工在赛狐网页端核对。"}


# ---------------------------------------------------------------------------
# record：把一条执行结果追加 logs/ad_changes.jsonl
# ---------------------------------------------------------------------------

_RECORD_REQUIRED = ("tool", "target_id", "field", "before", "after", "status")
_RECORD_STATUS = ("success", "failed", "skipped")


def record_change(change: Dict[str, Any], log_path: Path) -> Dict[str, Any]:
    """校验并追加一条执行结果到变更日志（jsonl），返回实际写入的记录。

    change.json 由 agent 依据执行清单里的 ``record`` 模板填写：
      必填：tool / target_id / field / before / after / status(success|failed|skipped)；
      强烈建议：operator、contract_version（当次执行用的版本）、
                read_back{"ok": bool, "actual": 实际回读值}。

    确定性防线：``status=success`` 但 ``read_back.ok=false`` → 强制改记 ``failed``
    （铁律：回读不一致即失败，不能只信网关返回）。
    """
    missing = [k for k in _RECORD_REQUIRED if k not in change]
    if missing:
        raise ValueError(f"change.json 缺少必填字段：{missing}（schema 见 workflow.md）")
    if change["status"] not in _RECORD_STATUS:
        raise ValueError(f"status 必须是 {'/'.join(_RECORD_STATUS)}，收到：{change['status']!r}")

    rec = dict(change)
    rb = rec.get("read_back")
    if rec["status"] == "success" and isinstance(rb, dict) and rb.get("ok") is False:
        rec["status"] = "failed"
        rec["status_note"] = "record 防线：read_back.ok=false → 按失败记录"
    rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    rec.setdefault("target_type", "campaign")
    rec.setdefault("risk", "L2")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def cmd_record(args: argparse.Namespace) -> int:
    try:
        change = json.loads(Path(args.change).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"❌ change.json 无法读取：{exc}", file=sys.stderr)
        return 2
    if not isinstance(change, dict):
        print("❌ change.json 必须是单个 JSON 对象（一条执行结果）。", file=sys.stderr)
        return 2
    log = Path(args.log) if args.log else ad_changes_path()
    try:
        rec = record_change(change, log)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    print(f"✅ 已追加变更记录（status={rec['status']}）→ {log}")
    print(json.dumps(rec, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# verify：写后回读 vs 期望的确定性比对
# ---------------------------------------------------------------------------


def _loose_equal(a: Any, b: Any) -> bool:
    """宽松比较（容忍 "100" vs 100、100.0 vs 100；与 approval 口径一致）。"""
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def _rows_from_actual(actual: Any) -> Tuple[List[Dict[str, Any]], str]:
    """从 actual 输入提取「行列表」。支持两种形态：

    1. **信封形态**（agent 把回读 action 的完整信封存盘）：含 status/data 键 →
       自动识别 itemList/rows/reportList；
    2. **判定形态**：``{"items": [{"index", "target_id", "field", "actual"}, ...]}``
       （agent 已从回读中摘出实际值）。
    """
    if isinstance(actual, dict) and isinstance(actual.get("data"), (dict, type(None))):
        data = actual.get("data") or {}
        for key in ("itemList", "rows", "reportList"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)], "envelope"
        return [], "envelope"
    if isinstance(actual, dict) and isinstance(actual.get("items"), list):
        return [r for r in actual["items"] if isinstance(r, dict)], "verdict"
    return [], "unknown"


def _match_id_fields(item: Dict[str, Any]) -> List[str]:
    """按对象类型给出回读行的候选 ID 字段（用于信封形态匹配）。"""
    tool = str(item.get("tool_id") or item.get("tool") or "")
    if tool.startswith("create_sp_negative"):
        return ["keywordText"]
    ttype = str(item.get("target_type") or "")
    if ttype == "campaign":
        return ["campaignId"]
    return ["keywordId", "targetId", "adId"]


def verify_proposal(proposal_payload: Dict[str, Any], actual_payload: Any) -> Dict[str, Any]:
    """对比提案期望与写后回读，返回逐项差异（纯函数，零网络）。

    :returns: {"rows": [{index, target_id, field, expected, actual, ok, verdict}],
               "ok": bool, "not_compared": [index…]}
    """
    items = proposal_payload.get("items") or []
    rows, shape = _rows_from_actual(actual_payload)
    out_rows: List[Dict[str, Any]] = []
    not_compared: List[int] = []

    for it in items:
        idx = it.get("index") or 0
        tid = str(it.get("target_id"))
        field = str(it.get("field"))
        expected = it.get("after")
        entry: Dict[str, Any] = {"index": idx, "target_id": tid, "field": field, "expected": expected}

        if shape == "verdict":
            hit = next((r for r in rows
                        if str(r.get("target_id")) == tid
                        or int(r.get("index") or 0) == idx), None)
            if hit is None:
                entry.update(actual=None, ok=False, verdict="actual 中缺少该项（未回读？）")
            else:
                actual = hit.get("actual")
                entry.update(actual=actual,
                             ok=_loose_equal(actual, expected),
                             verdict="一致" if _loose_equal(actual, expected) else "回读与期望不一致")
        elif shape == "envelope":
            id_fields = _match_id_fields(it)
            hit = next((r for r in rows
                        if any(str(r.get(f)) == (tid if f != "keywordText" else str(expected))
                               for f in id_fields if r.get(f) is not None)), None)
            if hit is None:
                entry.update(actual=None, ok=False,
                             verdict=f"回读信封中未找到 {tid}（游标未翻全或写未生效）")
            else:
                actual = hit.get(field)
                entry.update(actual=actual,
                             ok=_loose_equal(actual, expected),
                             verdict="一致" if _loose_equal(actual, expected) else "回读与期望不一致")
        else:
            entry.update(actual=None, ok=False, verdict="actual 形态无法识别（信封或 items 判定表）")
            not_compared.append(idx)
        out_rows.append(entry)

    ok = bool(out_rows) and all(r["ok"] for r in out_rows)
    return {"rows": out_rows, "ok": ok, "not_compared": not_compared}


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        proposal_payload = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        actual_payload = json.loads(Path(args.actual).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"❌ 文件无法读取：{exc}", file=sys.stderr)
        return 2

    result = verify_proposal(proposal_payload, actual_payload)
    print(f"{'✅ 全部一致' if result['ok'] else '❌ 存在差异'}"
          f"（{sum(1 for r in result['rows'] if r['ok'])}/{len(result['rows'])} 项通过）")
    print("| # | 对象 | 字段 | 期望 | 回读实际 | 结论 |")
    print("|---|---|---|---|---|---|")
    for r in result["rows"]:
        print(f"| {r['index']} | {r['target_id']} | {r['field']} | {r['expected']!r} "
              f"| {r['actual']!r} | {'✅ ' if r['ok'] else '❌ '}{r['verdict']} |")
    if not result["rows"]:
        print("（提案为空，无项可比对。）")
        return 0
    return 0 if result["ok"] else 2


# ---------------------------------------------------------------------------
# 子命令：diagnose / execute
# ---------------------------------------------------------------------------


def cmd_diagnose(args: argparse.Namespace) -> int:
    opt_cfg = load_optimizer()
    thresholds = load_thresholds()
    shop_id = args.shop_id
    if not shop_id:
        print("缺少 --shop-id（决定 campaigns_<shopId>.json 等落盘文件名）。", file=sys.stderr)
        return 2

    raw = Path(args.raw_dir) if args.raw_dir else boot.raw_dir()
    if not raw.is_absolute():
        raw = boot.SKILL_ROOT / raw
    try:
        ad_data = ad_fetch.load_from_raw(raw, shop_id, args.start, args.end, opt_cfg)
    except ad_fetch.AdFetchError as exc:
        print(f"❌ 落盘数据不满足诊断前提：{exc}", file=sys.stderr)
        print("   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」"
              "调用 mcp__Sellfox-MCP__{search,action} 并落盘到 out/raw/，再重跑。", file=sys.stderr)
        return 1

    findings = diag.diagnose(ad_data, thresholds, opt_cfg)
    items = diag.build_change_items(findings, opt_cfg)
    proposal = Proposal(skill="ad-optimizer",
                        title=f"广告优化提案 {args.start}~{args.end}",
                        items=items, risk_level="L2")
    try:
        proposal.validate(limits=thresholds.get("approval"))
    except Exception as exc:  # ApprovalError
        print(f"⚠️ 提案未通过强制约束校验，已清空提案待人工处理：{exc}", file=sys.stderr)
        proposal.items = []

    report = render_report(ad_data, findings, proposal, _sources_markdown(),
                           start_date=args.start, end_date=args.end)
    out_md = boot.output_path(f"ad_proposal_{args.start}_{args.end}.md")
    out_md.write_text(report, encoding="utf-8")
    out_json = boot.output_path(f"ad_proposal_{args.start}_{args.end}.json")
    save_proposal(out_json, proposal, shop_id=ad_data.shop_id, start=args.start, end=args.end,
                  demo_fingerprint=ad_data.demo_fingerprint)

    print(report)
    print(f"\n（已写入 {out_md} 与 {out_json}）")
    print(f"（提示：{_WRITE_PROTOCOL_NOTE}）")
    return 0


def _execution_checklist(
    selected: Sequence[ChangeItem], proposal: Proposal, meta: Dict[str, Any],
    shop_id: str, operator: str,
) -> Dict[str, Any]:
    """构造「执行清单」负载：被批准项 × tool_id × 参数模板 × before 快照。"""
    entry_items: List[Dict[str, Any]] = []
    for it in selected:
        record_tpl = {
            "tool": it.tool, "target_type": it.target_type, "target_id": it.target_id,
            "field": it.field, "before": it.before, "after": it.after,
            "reason": it.reason, "action": it.action, "risk": it.risk,
            "extra": it.extra or {}, "index": it.index,
            "status": "success | failed（执行后填写）",
            "operator": operator or "unknown",
            "contract_version": "<执行前重取的当次版本>",
            "read_back": {"ok": "true | false（回读比对结果）", "actual": "<回读实际值>"},
        }
        entry_items.append({
            "index": it.index,
            "tool_id": it.tool,
            "contract_version": CONTRACT_VERSION_PLACEHOLDER,
            "target_type": it.target_type, "target_id": it.target_id,
            "field": it.field, "before": it.before, "after": it.after,
            "reason": it.reason, "risk": it.risk,
            "params": _write_args(it, shop_id),
            "read_back_plan": _read_back_plan(it, shop_id),
            "record_template": record_tpl,
        })
    return {
        "meta": {
            "skill": proposal.skill, "title": proposal.title,
            "proposal": meta.get("title"), "shop_id": shop_id, "operator": operator,
            "approved_indexes": [it.index for it in selected],
            "start_date": meta.get("start_date"), "end_date": meta.get("end_date"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": False,
            "warning": _WRITE_PROTOCOL_NOTE,
            "protocol": (
                "写操作由 agent 执行（脚本零网络）：① 本清单已过人工审批与累计闸；"
                "② 逐项执行前重新 search 该 tool_id 取当次 contract_version；"
                "③ 调 mcp__Sellfox-MCP__action(tool_id, params, contract_version)；"
                "④ 按 read_back_plan 回读目标对象；⑤ 填 record_template 存为 change.json，"
                "执行 `python scripts/optimize.py record --change change.json`；"
                "⑥ 全部执行完跑 `optimize.py verify` 比对；"
                "⑦ partial/failed 一律不重放（见 workflow.md 写操作 agent 执行协议）。"
            ),
        },
        "items": entry_items,
    }


def cmd_execute(args: argparse.Namespace) -> int:
    """输出「执行清单」—— 不调用任何写工具（v3：写操作由 agent 逐项调 action）。"""
    thresholds = load_thresholds()
    loaded = load_proposal(Path(args.proposal))
    meta, proposal = loaded["meta"], loaded["proposal"]
    shop_id = args.shop_id or meta.get("shop_id")

    if meta.get("demo_fingerprint") and not args.allow_demo:
        print(f"⚠️ 检测到演示环境指纹「{meta['demo_fingerprint']}」，默认阻断写操作。"
              f"确需执行请显式传 --allow-demo。", file=sys.stderr)
        return 3
    if not shop_id:
        print("缺少 shop_id（提案 meta 与 --shop-id 均为空）。", file=sys.stderr)
        return 2

    decision, approved = parse_confirmation(args.approve or "", total=len(proposal.items))
    if decision == "unclear":
        print("审批文本无法识别，未生成执行清单（不得默认执行）。", file=sys.stderr)
        return 2
    if decision == "reject":
        print("已驳回，未执行任何变更。")
        return 0
    selected = proposal.items if decision == "approve_all" else proposal.approved_subset(approved)
    if not selected:
        print("批准集合为空，未执行。")
        return 0

    # 累计闸：防连续调用绕过单次限额（读 logs/ad_changes.jsonl）
    gate = AccumulatorGate(str(ad_changes_path()), limits=thresholds.get("approval"))
    violations = gate.check(selected)
    if violations:
        print("累计闸拦截，未生成执行清单：\n- " + "\n- ".join(violations), file=sys.stderr)
        return 4

    checklist = _execution_checklist(selected, proposal, meta, shop_id, args.operator)
    start, end = meta.get("start_date") or "na", meta.get("end_date") or "na"
    out_json = boot.output_path(f"ad_execute_{start}_{end}.json")
    out_md = boot.output_path(f"ad_execute_{start}_{end}.md")
    out_json.write_text(json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# 广告写操作执行清单 | {start} ~ {end}",
        "",
        f"- 店铺：`{shop_id}` ｜ 操作人：`{args.operator or 'unknown'}` ｜ "
        f"被批准项：{[it.index for it in selected]}（共 {len(selected)} 条）",
        f"- ⚠️ {_WRITE_PROTOCOL_NOTE}",
        "",
        "按 `references/workflow.md`「写操作 agent 执行协议」逐项执行："
        "重取契约 → action → 回读 → record → （全部完成后）verify。partial/failed 不重放。",
        "",
    ]
    for e in checklist["items"]:
        md_lines += [
            f"## #{e['index']} `{e['tool_id']}` → {e['target_id']}.{e['field']}"
            f"：{e['before']!r} → {e['after']!r}",
            "",
            f"- 理由：{e['reason']} ｜ 风险：{e['risk']} ｜ 回滚值（before 快照）：`{e['before']!r}`",
            f"- action 参数模板（contract_version 执行前重取）：",
            "",
            "```json",
            json.dumps({"tool_id": e["tool_id"], "contract_version": None,
                        "params": e["params"]}, ensure_ascii=False, indent=2),
            "```",
            "",
            f"- read-back 计划：`{e['read_back_plan'].get('tool_id')}` 匹配 "
            f"`{e['read_back_plan'].get('id_field')}={e['read_back_plan'].get('id_value')}`，"
            f"期望 `{e['read_back_plan'].get('field')}={e['read_back_plan'].get('expected')!r}`",
            "",
            "执行后把 record_template 填好实际结果存为 change.json，运行：",
            "",
            "```bash",
            "python scripts/optimize.py record --change change.json",
            "```",
            "",
        ]
    out_md.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"✅ 执行清单已生成（脚本未执行任何写操作）：\n  {out_md}\n  {out_json}")
    print(f"下一步由 agent 按清单逐项执行：{_WRITE_PROTOCOL_NOTE}")
    print(f"每项执行完：python scripts/optimize.py record --change change.json")
    print(f"全部执行完：python scripts/optimize.py verify --proposal {args.proposal} --actual <回读落盘.json>")
    return 0


# ---------------------------------------------------------------------------
# selftest：完全离线（不调 MCP、不写生产日志）
# ---------------------------------------------------------------------------


def cmd_selftest(_args: argparse.Namespace) -> int:
    """离线自检：合成数据跑通 诊断 → 提案 → 渲染 + record/verify helper（不调用 MCP）。"""
    from ad_fetch import AdData

    ad_data = AdData(
        shop_id="MOCK-SHOP-1",
        campaigns=[
            {"campaignId": "C1", "name": "演示-零转化活动", "state": "enabled", "budget": 100, "servingStatus": "CAMPAIGN_PAUSED"},
            {"campaignId": "C2", "name": "演示-优质活动", "state": "enabled", "budget": 50, "servingStatus": "CAMPAIGN_ACTIVE"},
        ],
        keywords=[
            {"keywordId": "K1", "keywordText": "free shipping", "state": "enabled", "bid": 2.0, "campaignId": "C1"},
            {"keywordId": "K2", "keywordText": "cheap widget", "state": "enabled", "bid": 1.0, "campaignId": "C2"},
        ],
        perf_campaign=[
            {"campaignId": "C1", "campaignName": "演示-零转化活动", "cost": "120.5", "orders": "0",
             "sales": "0", "clicks": "90", "impressions": "5000"},
            {"campaignId": "C2", "campaignName": "演示-优质活动", "cost": "8.0", "orders": "5",
             "sales": "80", "clicks": "40", "impressions": "300", "acos": "10", "roas": "10"},
        ],
        perf_keyword=[
            {"keywordId": "K1", "keywordText": "free shipping", "cost": "60", "orders": "0", "clicks": "70"},
            {"keywordId": "K2", "keywordText": "cheap widget", "cost": "20", "orders": "1",
             "sales": "60", "clicks": "30", "acos": "45"},
        ],
        perf_source="download_task", perf_scope="campaign",
        truncations=["本次共取回 2 行（未截断）。"],
        demo_fingerprint="演示",
    )
    opt_cfg = load_optimizer()
    thresholds = load_thresholds()
    findings = diag.diagnose(ad_data, thresholds, opt_cfg)
    items = diag.build_change_items(findings, opt_cfg)
    proposal = Proposal(skill="ad-optimizer", title="离线自检提案", items=items)
    proposal.validate(limits=thresholds.get("approval"))
    print(render_report(ad_data, findings, proposal, _sources_markdown(),
                        start_date="2026-09-23", end_date="2026-09-29"))

    # --- record / verify helper 的离线往返（临时目录，不写生产日志）---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "ad_changes.jsonl"
        rec_ok = record_change({
            "tool": "edit_sp_campaign", "target_type": "campaign", "target_id": "C2",
            "field": "budget", "before": 50, "after": 60, "reason": "selftest",
            "index": 1, "operator": "selftest", "status": "success",
            "contract_version": "v-selftest",
            "read_back": {"ok": True, "actual": 60},
        }, log)
        rec_flip = record_change({  # read_back.ok=False 必须被改记 failed
            "tool": "edit_sp_campaign", "target_type": "campaign", "target_id": "C1",
            "field": "state", "before": "enabled", "after": "paused", "reason": "selftest",
            "index": 2, "operator": "selftest", "status": "success",
            "read_back": {"ok": False, "actual": "enabled"},
        }, log)
        assert rec_ok["status"] == "success" and rec_flip["status"] == "failed", "record 防线失效"
        gate = AccumulatorGate(str(log))
        assert not gate.check([ChangeItem(tool="edit_sp_campaign", target_type="campaign",
                                          target_id="C9", field="budget", before=1, after=2)]), \
            "无历史对象不应被拦截"
        assert gate.check([ChangeItem(tool="edit_sp_campaign", target_type="campaign",
                                      target_id="C2", field="budget", before=50, after=60)]), \
            "C2 今日已成功变更过，应被累计闸去重拦截"

        # verify：信封形态（写后回读）与判定形态各测一次
        save_proposal(tmp / "p.json", proposal, shop_id="MOCK-SHOP-1",
                      start="2026-09-23", end="2026-09-29", demo_fingerprint="演示")
        p_payload = json.loads((tmp / "p.json").read_text(encoding="utf-8"))
        for it in p_payload["items"]:
            it["target_id"] = {"budget": "C2", "state": "C1"}.get(it["field"], it["target_id"])
        env_actual = {"status": "complete", "data": {"itemList": [
            {"campaignId": "C1", "state": "paused", "budget": 100},
            {"campaignId": "C2", "state": "enabled", "budget": 60},
        ]}}
        res = verify_proposal(p_payload, env_actual)
        assert res["ok"], f"verify 应全部一致：{res}"
        res_bad = verify_proposal(
            p_payload, {"items": [{"index": 1, "target_id": "C2", "field": "budget", "actual": 55}]})
        assert not res_bad["ok"], "verify 应发现差异"

    print(f"\n✅ 自检通过：findings={len(findings)}，可自动执行提案={len(items)}，"
          "record/verify helper 往返一致（临时目录，未写生产日志）")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="06-ad-optimizer 广告诊断与优化（v3：脚本零网络，写操作由 agent 执行）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("diagnose", help="读 out/raw/ 落盘 → 诊断 → 生成 dry-run 提案")
    p_d.add_argument("--shop-id", required=True, help="决定 campaigns_<shopId>.json 等落盘文件名")
    p_d.add_argument("--start", required=True, help="yyyy-MM-dd")
    p_d.add_argument("--end", required=True, help="yyyy-MM-dd")
    p_d.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    p_d.set_defaults(func=cmd_diagnose)

    p_e = sub.add_parser("execute",
                         help="解析批准 + 累计闸 → 输出执行清单（不调用写工具，由 agent 执行）")
    p_e.add_argument("--proposal", required=True, help="proposal.json 路径")
    p_e.add_argument("--approve", required=True, help='如 "批准 1,3,5" / "批准全部" / "驳回"')
    p_e.add_argument("--operator", default="", help="操作人标识（飞书 open_id）")
    p_e.add_argument("--shop-id", default="", help="覆盖提案中的 shop_id")
    p_e.add_argument("--allow-demo", action="store_true", help="演示环境放行（默认阻断）")
    p_e.set_defaults(func=cmd_execute)

    p_r = sub.add_parser("record", help="把一条执行结果追加 logs/ad_changes.jsonl（agent 逐项调用）")
    p_r.add_argument("--change", required=True, help="change.json（一条执行结果，schema 见 workflow.md）")
    p_r.add_argument("--log", default=None, help="覆盖变更日志路径（默认 logs/ad_changes.jsonl）")
    p_r.set_defaults(func=cmd_record)

    p_v = sub.add_parser("verify", help="对比写后回读与提案期望，输出差异")
    p_v.add_argument("--proposal", required=True, help="ad_proposal_*.json")
    p_v.add_argument("--actual", required=True,
                     help="回读数据 JSON：完整信封（含 data.itemList/rows）或 {items:[{index,target_id,field,actual}]}")
    p_v.set_defaults(func=cmd_verify)

    p_s = sub.add_parser("selftest", help="离线自检（不调用 MCP，不写生产日志）")
    p_s.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

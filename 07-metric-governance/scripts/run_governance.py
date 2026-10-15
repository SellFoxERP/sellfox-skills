#!/usr/bin/env python3
"""指标治理 CLI 入口（run_governance.py）—— 技能 07-metric-governance（v3：零网络）。

v3 分工：取数面 = agent 调 `mcp__Sellfox-MCP__{help,search,action}` 按取数计划落盘
`out/raw/`；本脚本 = 计算面（落盘检查 → 比对 → dry-run 提案），**零网络**。
**脚本永不直接调写工具**：add/update_custom_report_index 由 agent 按批准逐项执行。

用法::

    # 0) agent 先按 references/workflow.md 取数计划把信封落盘 out/raw/

    # 1) 落盘检查 → 比对 → dry-run 提案（默认）
    python scripts/run_governance.py

    # 2) 人工批准后生成「执行清单」（不是写入！交给 agent 按协议逐项执行）
    python scripts/run_governance.py --execute --approve "批准 1,3"

    # 3) agent 逐项执行后：把执行结果 JSON 追加变更记录（ad_changes.jsonl）
    python scripts/run_governance.py --record out/exec_results.json \
        --manifest out/execute_manifest_<ts>.json

    # 4) agent 重取回读落盘（custom_indices_verify_<page>.json）后：公式回读比对
    python scripts/run_governance.py --verify --manifest out/execute_manifest_<ts>.json

安全默认：
  * 不带 ``--execute`` 一律 dry-run；``--execute`` 也**只生成执行清单**，绝不写入。
  * 现有指标落盘不可信（失败页/损坏/截断）时不生成提案——防「误判缺失→重复建指标」。
  * 演示环境命中指纹时默认不出执行清单（``--allow-demo-write`` 可显式覆盖，仍加水印）。
  * 生成执行清单前过累计闸；写后必须回读比对（--verify），失败/不一致不重放。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

_SKILL_ROOT = Path(__file__).resolve().parent.parent  # 07-metric-governance/


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="赛狐自定义指标治理（v3：读落盘 + agent 执行写）")
    p.add_argument("--skill-config", default=None, help="技能配置路径（默认 config/metric_governance.yaml）")
    p.add_argument("--shared-root", default=None, help="覆盖 _shared/ 路径")
    p.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    p.add_argument("--out-dir", default=None, help="输出目录（默认 <skill>/out）")
    p.add_argument("--execute", action="store_true",
                   help="生成「执行清单」供 agent 按批准逐项执行（脚本不直接写）")
    p.add_argument("--approve", default='', help='批准文本，如 "批准 1,3" / "批准全部" / "驳回"')
    p.add_argument("--operator", default=os.environ.get("USER", "unknown"), help="操作人（写入 ad_changes.jsonl）")
    p.add_argument("--max-rows", type=int, default=None, help="覆盖 max_rows 安全阀")
    p.add_argument("--report-key", action="append", default=None,
                   help="覆盖 target_reports（可多次；仅影响输出的报表过滤提示，实际过滤在取数面）")
    p.add_argument("--allow-demo-write", action="store_true",
                   help="演示环境也生成执行清单（谨慎；仍加水印）")
    p.add_argument("--manifest", default=None, help="--record/--verify 所用的执行清单路径")
    p.add_argument("--record", default=None, metavar="RESULTS_JSON",
                   help="把 agent 执行结果 JSON 追加变更日志（配合 --manifest）")
    p.add_argument("--verify", action="store_true",
                   help="写后回读公式比对（读 custom_indices_verify_<page>.json，配合 --manifest）")
    p.add_argument("--verify-file", action="append", default=None,
                   help="指定回读落盘文件（可多次；默认自动发现 custom_indices_verify_*.json）")
    return p.parse_args(argv)


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _resolve_to(value: Optional[str], default: Path) -> Path:
    """相对路径按当前工作目录解析（与 --skill-config/--record 行为一致）；缺省用默认值。"""
    if value:
        return Path(value).expanduser().resolve()
    return default


# ---------------------------------------------------------------------------
# 子命令：record（agent 执行结果 → 变更日志）
# ---------------------------------------------------------------------------


def cmd_record(gov, args) -> int:
    if not args.manifest:
        print("❌ --record 需要 --manifest <execute_manifest.json>（--execute 生成的执行清单）。")
        return 1
    try:
        manifest = gov.load_json(args.manifest)
    except Exception as exc:
        print(f"❌ 执行清单无法读取：{exc}")
        return 1
    try:
        results = gov.load_json(args.record)
    except Exception as exc:
        print(f"❌ 执行结果文件无法读取：{exc}")
        return 1
    if not isinstance(results, list):
        print("❌ 执行结果文件必须是 JSON 数组：[{\"index\": 1, \"status\": \"success\", "
              "\"contract_version\": \"...\", \"note\": \"...\"}, ...]")
        return 1

    cfg = gov.load_skill_config(args.skill_config)
    log_path = str(cfg.get("change_log_path") or "ad_changes.jsonl")
    summary = gov.record_execution(manifest, results, operator=args.operator, log_path=log_path)

    print("## 变更记录写入结果")
    for status, idxs in summary["recorded"].items():
        icon = {"success": "✅", "failed": "❌", "skipped": "⏭️"}.get(status, "•")
        print(f"{icon} {status}: 序号 {idxs}")
    for r in summary["unknown"]:
        print(f"⚠️ 清单中无此序号，未记录：{r}")
    for r in summary["invalid"]:
        print(f"⚠️ 结果条目非法（index/status 缺失或 status 不在 success|failed|skipped），未记录：{r}")
    print(f"变更日志：{Path(log_path).resolve()}")
    print("提醒：status=success 只应在 --verify 回读一致后记录；失败/不一致不重放，列「需人工核查」。")
    return 0


# ---------------------------------------------------------------------------
# 子命令：verify（写后回读公式比对）
# ---------------------------------------------------------------------------


def cmd_verify(gov, args) -> int:
    if not args.manifest:
        print("❌ --verify 需要 --manifest <execute_manifest.json>（--execute 生成的执行清单）。")
        return 1
    try:
        manifest = gov.load_json(args.manifest)
    except Exception as exc:
        print(f"❌ 执行清单无法读取：{exc}")
        return 1

    cfg = gov.load_skill_config(args.skill_config)
    raw_dir = _resolve_to(args.raw_dir, _SKILL_ROOT / "out" / "raw")
    rows, notes = gov.gather_verify_rows(raw_dir, args.verify_file)
    for n in notes:
        print(n)
    if not rows and any(n.startswith("❌") for n in notes):
        return 1

    results = gov.verify_against_rows(manifest, rows, cfg)
    out_dir = _resolve(_SKILL_ROOT, args.out_dir or "out")
    out_path = Path(gov.save_json(out_dir / f"verify_{_ts()}.json", {
        "manifest": args.manifest,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verify_rows": len(rows),
        "results": results,
    }))

    icons = {"match": "✅", "mismatch": "❌", "not_found": "⚠️", "unknown_field": "⚠️"}
    print("\n## 写后回读比对")
    print("| 序号 | 指标 | 期望公式 | 回读公式 | 判定 |")
    print("|---|---|---|---|---|")
    for r in results:
        print(f"| {r['index']} | {r['index_name']} | {r['expected']} | {r['actual'] or '—'} "
              f"| {icons.get(r['verdict'], '•')} {r['verdict_text']} |")
    n_match = sum(1 for r in results if r["verdict"] == "match")
    n_bad = len(results) - n_match
    print(f"\n一致 {n_match} / 共 {len(results)}；明细已写出：{out_path}")
    if n_bad:
        print("⚠️ 存在不一致/无法判定项：不重放、不自动重试；核对后列「需人工核查」，不得记 success。")
    return 0 if n_bad == 0 else 2


# ---------------------------------------------------------------------------
# 主流程：落盘检查 → 比对 → dry-run 提案（--execute 时再生成执行清单）
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.shared_root:
        os.environ["SELLFOX_SHARED_ROOT"] = args.shared_root

    # 延迟导入：index_governor 在导入期解析 shared_root，故先设好环境变量。
    import index_governor as gov  # noqa: WPS433

    if args.record:
        return cmd_record(gov, args)
    if args.verify:
        return cmd_verify(gov, args)

    cfg = gov.load_skill_config(args.skill_config)
    if args.max_rows is not None:
        cfg["max_rows"] = args.max_rows
    if args.report_key:
        cfg["target_reports"] = args.report_key
    thresholds = gov.load_thresholds()
    out_dir = _resolve_to(args.out_dir, _SKILL_ROOT / "out")
    raw_dir = _resolve_to(args.raw_dir, out_dir / "raw")

    # 报表过滤提示（实际过滤在取数面：agent 调用时传 report_id_list）。
    reports = gov.load_shared_reports()
    report_ids, missing_reports = gov.target_report_ids(cfg, reports)
    if missing_reports:
        print(
            "⚠️ 以下报表 report_id 为空，取数计划将按无报表过滤执行"
            "（口径降级：治理范围=全部「我的」指标）：\n"
            f"   {', '.join(missing_reports)} → 助手可自动发现报表；当前仅按已声明的只读范围分析。"
            "不能因目标报表缺失扩大批准的写入范围。"
        )

    desired = [gov.DesiredIndex.from_dict(d) for d in (cfg.get("desired_indices") or [])]
    if not desired:
        print("⚠️ config/metric_governance.yaml 的 desired_indices 为空，无可治理项。")
        return 0

    # Step 1：官方指标目录（读落盘 stat_index_op1/2.json，防御性解析）。
    trees, tree_notes, tree_demo = gov.load_official_trees(raw_dir)
    for n in tree_notes:
        print(n)
    for op in sorted(trees):
        print(f"官方指标目录(op_type={op}) 节点数：{len(trees[op])}（基线参照，不参与差异判定）")

    # Step 2：现有自定义指标（读落盘 custom_indices_<page>.json，多页合并）。
    state = gov.load_existing_indices(raw_dir, cfg)
    for n in state.notes:
        print(n)
    demo_fp = state.demo_fingerprint or tree_demo
    if demo_fp and cfg.get("demo_policy", {}).get("watermark", True):
        print(f"⚠️ 演示数据，非真实经营数据（指纹：{demo_fp}）——只可验证连通性与参数格式，"
              "演示环境默认只跑 dry-run。")
    print(f"现有自定义指标：{len(state.rows)} 行（落盘 {raw_dir}，{state.pages_ok} 页成功）")

    # Step 3：差异比对。
    plan = gov.build_plan(state.rows, desired)
    print("差异比对：" + "；".join(plan.describe_lines()))
    if plan.pending:
        print("以下指标公式未人工确认（needs_human），不进入提案：")
        for d in plan.pending:
            print(f"  - {d.name}（类型 {d.type}；依据字段 {', '.join(d.source_fields) or '—'}）")

    review_lines = ["# 指标治理检查结果", "", f"已读取自定义指标：{len(state.rows)} 行。", "",
                    *plan.describe_lines(), "", "## 待确认公式"]
    review_lines.extend(f"- {d.name}：{', '.join(d.source_fields) or '字段待确认'}" for d in plan.pending)
    review_lines.extend(["", "## 数据说明", *tree_notes, *state.notes])
    if demo_fp:
        review_lines.insert(1, "> 演示数据；本次仅生成检查结果，不写入业务系统。")
    review_path = raw_dir.parent / "governance_report.md"
    review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    print(f"检查报告：{review_path}")
    # 取数可信门：落盘不可信时不生成提案（防误判缺失→重复建指标）。
    if not state.reliable:
        print(
            "⛔ 现有指标取数不可信（存在失败/损坏页或截断），本轮不生成提案——\n"
            "   「指标缺失」可能只是「未取到」，据此新增会在赛狐重复建指标。\n"
            "   请按 references/workflow.md 取数计划补齐 out/raw/custom_indices_<page>.json 后重跑。"
        )
        return 2
    if not plan.adds and not plan.updates:
        print("存在待确认公式，本次未生成变更提案。" if plan.pending else "✅ 无需变更（无新增/更新项）。")
        return 0

    # Step 4：提案（dry-run）。
    proposal = gov.build_proposal(plan)
    limits = thresholds.get("approval") or {}
    try:
        proposal.validate(limits=limits)
    except gov.ap.ApprovalError as exc:
        print(f"⚠️ 提案超出强制约束：{exc}\n   将按单次上限分批审批。")
    ts = _ts()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"proposal_{ts}.md"
    md_path.write_text(proposal.render_markdown(limits), encoding="utf-8")
    prop_md = str(md_path)
    prop_json = gov.save_json(out_dir / f"proposal_{ts}.json", gov.proposal_to_dict(proposal))
    print("\n" + proposal.render_markdown(limits))
    print(f"\n提案已写出：{prop_md} / {prop_json}")
    print("注：提案项 contract_version 为 null 占位——执行前由 agent 重取写契约（search detail=true）。")

    if not args.execute:
        print('\n（dry-run 模式，未写入任何指标。确认后运行 --execute --approve "批准 1,3" 生成执行清单，'
              "由 agent 按协议逐项执行。）")
        return 0

    # Step 5：人工确认解析。
    decision, approved = gov.ap.parse_confirmation(args.approve, total=len(proposal.items))
    if decision == "unclear":
        print('⚠️ 无法解析批准文本，请回复「批准 1,3」/「批准全部」/「驳回」。未生成执行清单。')
        return 1
    if decision == "reject":
        print("已驳回，未生成执行清单。")
        return 0
    items = proposal.items if decision == "approve_all" else proposal.approved_subset(approved)
    if not items:
        print("未选中任何条目，未生成执行清单。")
        return 0

    # Step 6a：累计闸（读 ad_changes.jsonl）。
    log_path = str(cfg.get("change_log_path") or "ad_changes.jsonl")
    violations = gov.gate_check(items, thresholds, log_path)
    if violations:
        print("⛔ 累计闸拦截：\n  " + "\n  ".join(violations))
        return 1

    # Step 6b：演示环境默认阻断（保持 dry-run）。
    demo_blocked = bool(demo_fp) and bool(cfg.get("demo_policy", {}).get("block_write_execute", True))
    if demo_blocked and not args.allow_demo_write:
        print("⛔ 演示环境默认阻断写入（--allow-demo-write 可显式覆盖）。本次只演练，不生成执行清单。")
        return 0

    # Step 6c：生成「执行清单」（脚本不写；agent 按批准逐项执行）。
    notes = list(state.notes) + list(tree_notes)
    if missing_reports:
        notes.append("口径降级：未指定报表，治理范围为全部非共享（「我的」）指标。")
    manifest = gov.build_execute_manifest(
        items, operator=args.operator, approval_text=args.approve,
        demo_fingerprint=demo_fp, notes=notes,
    )
    manifest_path = gov.save_json(out_dir / f"execute_manifest_{ts}.json", manifest)

    print(f"\n## 执行清单已生成：{manifest_path}")
    print(f"（脚本零网络、不直接调写工具；以下 {len(items)} 项由 agent 按批准逐项执行）")
    for step in manifest["protocol"]:
        print(f"  {step}")
    print(f"\n后续命令：")
    print(f"  记录结果：python scripts/run_governance.py --record <results.json> --manifest {manifest_path}")
    print(f"  回读比对：python scripts/run_governance.py --verify --manifest {manifest_path}")
    print(
        "⚠️ 未能覆盖（需人工核查）：index_formula 语法（无官方文档）；"
        "指标↔报表绑定（add 无 report_id 参数，需网页端操作）；回读字段名（响应未实测）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

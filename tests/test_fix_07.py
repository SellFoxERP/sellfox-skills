"""07-metric-governance 修复回归测试（2026-10-16 评测问题），合成/归档数据直测，零网络。

覆盖（对齐 out/eval-results-2026-10-16 与 out/eval-live-2026-10-16 的问题清单）：
1. P1：--verify 不再 NameError（_resolve → _resolve_to），判定表 + verify_*.json 产出，
   判定正确（match / mismatch / unknown_field）——归档 case2/case5/case7 复放；
2. 官方指标树递归全层级：30 节点（10 顶级 × 3 层）全量解析、层级计数、
   全量树落盘 JSON（广告分支 L2/L3 深层节点不再丢弃）——归档 live case4 复放；
3. pending 原因区分：公式 verified 但现有行缺真实 indexId →「待补真实 ID」，
   不再误标「公式未人工确认（needs_human）」——归档 case5 raw_noid 复放；
4. 回读补偿记录：verify 检出 mismatch 且原记录 success → 追加 type=amend 记录
   （不改写历史；累计闸仍只按原始 success 计数，amend 不额外计数）；
5. SKILL.md 速查表：update_custom_report_index 的 id 为必填（真实契约 required 含 id）。
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "07-metric-governance"
SCRIPTS = SKILL / "scripts"
OFFLINE = SKILL / "out" / "eval-2026-10-16"
LIVE = SKILL / "out" / "eval-live-2026-10-16"

# 与其他技能同名脚本隔离：以文件位置加载 index_governor，加载完恢复原值。
_SAVED_MODULES = {n: sys.modules.get(n) for n in ("index_governor",)}
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("index_governor", SCRIPTS / "index_governor.py")
gov = importlib.util.module_from_spec(_spec)
sys.modules["index_governor"] = gov
try:
    _spec.loader.exec_module(gov)
finally:
    for _n, _m in _SAVED_MODULES.items():
        if _m is None:
            sys.modules.pop(_n, None)
        else:
            sys.modules[_n] = _m

from approval import AccumulatorGate, ChangeItem  # noqa: E402  （index_governor 已挂 _shared/scripts）


def _run_cli(*args, cwd):
    return subprocess.run([sys.executable, str(SCRIPTS / "run_governance.py"), *args],
                          cwd=str(cwd), capture_output=True, text=True)


def _sandbox(*, raw_src: Path, files: dict = None) -> Path:
    """把归档用例复制进临时沙箱（change_log_path 一并改写到沙箱，绝不追加归档日志）。"""
    tmp = Path(tempfile.mkdtemp(prefix="fix07_"))
    shutil.copytree(raw_src, tmp / "raw")
    for name, src in (files or {}).items():
        if Path(src).exists():
            shutil.copy(src, tmp / name)
    cfg = tmp / "config.yaml"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        text = re.sub(r"^change_log_path: .*$", "change_log_path: ad_changes.jsonl",
                      text, flags=re.M)
        cfg.write_text(text, encoding="utf-8")
    return tmp


# ---------------------------------------------------------------------------
# 1. P1：--verify 路径修复（不再 NameError）
# ---------------------------------------------------------------------------


class VerifyNoNameErrorTest(unittest.TestCase):

    def test_source_has_no_undefined_resolve_call(self):
        src = (SCRIPTS / "run_governance.py").read_text(encoding="utf-8")
        self.assertNotIn("_resolve(", src, "未定义名 _resolve 不得再被调用")
        self.assertIn("def _resolve_to(", src)

    def test_replay_case2_verify_match_and_json_written(self):
        case = OFFLINE / "case2"
        manifest = sorted(case.glob("execute_manifest_*.json"))
        if not (manifest and (case / "raw" / "custom_indices_verify_1.json").exists()):
            self.skipTest("归档 case2 不存在")
        tmp = _sandbox(raw_src=case / "raw", files={"config.yaml": case / "config.yaml",
                                                    "manifest.json": manifest[-1]})
        proc = _run_cli("--verify", "--manifest", "manifest.json", "--raw-dir", "raw",
                        "--out-dir", "out", "--skill-config", "config.yaml", cwd=tmp)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertNotIn("NameError", combined)
        self.assertNotIn("Traceback", combined)
        self.assertIn("| 1 | 毛利率-公司口径 | grossProfitRate | grossProfitRate | ✅ 一致 |", proc.stdout)
        self.assertIn("| 3 | 广告成本占比-公司口径 | ABS(cpcCost)/productSales*100 | "
                      "ABS(cpcCost)/productSales*100 | ✅ 一致 |", proc.stdout)
        verify_files = list((tmp / "out").glob("verify_*.json"))
        self.assertEqual(len(verify_files), 1, "verify_*.json 必须产出")
        payload = json.loads(verify_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["verify_rows"], 5)  # 回读信封 5 行（3 无关指标 + 2 目标）
        self.assertEqual([r["verdict"] for r in payload["results"]], ["match", "match"])
        self.assertEqual(payload["amend_records"], [])

    def test_replay_case7_verify_mismatch_exit_2_no_amend_for_failed(self):
        case = OFFLINE / "case7"
        manifest = sorted(case.glob("execute_manifest_*.json"))
        if not (manifest and (case / "raw" / "custom_indices_verify_1.json").exists()):
            self.skipTest("归档 case7 不存在")
        # 先把「原记录 failed」的成功日志语义带进沙箱：复制归档日志（1 条 failed）
        tmp = _sandbox(raw_src=case / "raw", files={"config.yaml": case / "config.yaml",
                                                    "manifest.json": manifest[-1],
                                                    "ad_changes.jsonl": case / "ad_changes.jsonl"})
        proc = _run_cli("--verify", "--manifest", "manifest.json", "--raw-dir", "raw",
                        "--out-dir", "out", "--skill-config", "config.yaml", cwd=tmp)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, combined)
        self.assertNotIn("NameError", combined)
        self.assertIn("❌ 不一致", proc.stdout)
        self.assertIn("profit/sales*100", proc.stdout)  # 回读=旧公式（写未生效）
        # 原记录是 failed（本就按失败处理）→ 不追加补偿记录
        self.assertNotIn("补偿记录", proc.stdout)
        lines = (tmp / "ad_changes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "failed")

    def test_replay_case5_unknown_field_variant(self):
        case = OFFLINE / "case5"
        manifest = sorted(case.glob("execute_manifest_*.json"))
        unknown = case / "raw_unknown_field" / "custom_indices_verify_1.json"
        if not (manifest and unknown.exists()):
            self.skipTest("归档 case5 不存在")
        tmp = _sandbox(raw_src=case / "raw", files={"config.yaml": case / "config.yaml",
                                                    "manifest.json": manifest[-1]})
        shutil.copy(unknown, tmp / "raw" / "custom_indices_verify_1.json")
        proc = _run_cli("--verify", "--manifest", "manifest.json", "--raw-dir", "raw",
                        "--verify-file", "raw/custom_indices_verify_1.json",
                        "--out-dir", "out", "--skill-config", "config.yaml", cwd=tmp)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, combined)
        self.assertIn("回读字段名未知", proc.stdout)
        payload = json.loads(next((tmp / "out").glob("verify_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(payload["results"][0]["verdict"], "unknown_field")
        self.assertEqual(payload["amend_records"], [], "unknown_field 不追加补偿记录")


# ---------------------------------------------------------------------------
# 2. 官方指标树：递归全层级（30 节点）+ 全量树落盘
# ---------------------------------------------------------------------------


def _nested_tree(top: int = 10):
    """合成嵌套树：top 顶级 × 3 层（每节点 1 子）= top*3 节点，广告分支在 idx-1007。"""
    names = ["销售额", "利润", "访客数", "转化率", "客单价", "退款金额", "广告花费",
             "库存数量", "发货数量", "评价数量"]
    mapped = ["sales_amount", "profit", "visitor_count", "conversion_rate",
              "average_order_value", "refund_amount", "advertising_spend",
              "inventory_quantity", "shipped_quantity", "review_count"]
    tree = []
    for i in range(top):
        node = {"id": f"idx-10{i + 1:02d}", "name": names[i % len(names)],
                "level": "1", "mappedIndex": mapped[i % len(mapped)],
                "children": [{"id": f"idx-10{i + 1:02d}-01", "name": f"{names[i % len(names)]}子",
                              "level": "2", "mappedIndex": f"sub_{i}",
                              "children": [{"id": f"idx-10{i + 1:02d}-01-01", "name": f"{names[i % len(names)]}孙",
                                            "level": "3", "mappedIndex": f"leaf_{i}"}]}]}
        if node["mappedIndex"] == "advertising_spend":
            node["children"][0]["name"] = "广告销售额"
            node["children"][0]["mappedIndex"] = "ad_sales"
            node["children"][0]["children"][0]["name"] = "广告投入产出比"
            node["children"][0]["children"][0]["mappedIndex"] = "roas"
        tree.append(node)
    return tree


class RecursiveTreeTest(unittest.TestCase):

    TREE = _nested_tree()

    def test_normalize_tree_keeps_children(self):
        nodes = gov.normalize_tree(self.TREE)
        self.assertEqual(len(nodes), 10)
        self.assertTrue(nodes[0].get("children"), "children 不得被剥离")

    def test_tree_stats_counts_all_levels(self):
        total, levels = gov.tree_stats(self.TREE)
        self.assertEqual(total, 30)
        self.assertEqual(levels, {1: 10, 2: 10, 3: 10})

    def test_tree_summary_and_deep_nodes(self):
        summary = gov.tree_summary(self.TREE)
        self.assertEqual(summary["top_level"], 10)
        self.assertEqual(summary["total_nodes"], 30)
        self.assertEqual(summary["nodes_by_level"], {"1": 10, "2": 10, "3": 10})
        flat = [n for t in summary["tree"] for n in _walk(t)]
        names = {(n["name"], n["mapped_index"]) for n in flat}
        self.assertIn(("广告花费", "advertising_spend"), names)   # L1
        self.assertIn(("广告销售额", "ad_sales"), names)           # L2 深层节点不丢
        self.assertIn(("广告投入产出比", "roas"), names)            # L3 深层节点不丢

    def test_flat_forms_parse_defensively(self):
        # dict rows 形态同样取到顶级节点（children 保留）
        nodes = gov.normalize_tree({"rows": self.TREE})
        self.assertEqual(len(nodes), 10)
        self.assertEqual(gov.tree_stats(nodes)[0], 30)
        # 不可解析形态 → 空（不臆造）
        self.assertEqual(gov.normalize_tree({"resultCode": "0", "resultMsg": "ok"}), [])

    def test_replay_live_case4_full_tree_persisted(self):
        raw = LIVE / "case4" / "raw"
        if not (raw / "stat_index_op1.json").exists():
            self.skipTest("归档 live case4 不存在")
        tmp = _sandbox(raw_src=raw)
        proc = _run_cli("--raw-dir", "raw", "--out-dir", "out", cwd=tmp)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        for op in ("1", "2"):
            self.assertIn(f"官方指标目录(op_type={op}) 顶级 10 / 共 30 节点（L1 10，L2 10，L3 10）",
                          proc.stdout)
        tree_files = list((tmp / "out").glob("official_tree_*.json"))
        self.assertEqual(len(tree_files), 1, "全量树 JSON 必须落盘")
        payload = json.loads(tree_files[0].read_text(encoding="utf-8"))
        self.assertEqual({op: payload[op]["total_nodes"] for op in payload},
                         {"1": 30, "2": 30})

        def walk(n):
            yield n
            for c in n["children"]:
                yield from walk(c)

        flat = [n for op in payload for n in (x for t in payload[op]["tree"] for x in walk(t))]
        ad = {n["mapped_index"] for n in flat
              if (n["mapped_index"] or "") in ("advertising_spend", "ad_sales", "roas")}
        self.assertEqual(ad, {"advertising_spend", "ad_sales", "roas"},
                         "广告分支三层节点必须全部在落盘树中")


def _walk(node):
    yield node
    for c in node["children"]:
        yield from _walk(c)


# ---------------------------------------------------------------------------
# 3. pending 原因区分：缺真实 ID ≠ 公式未确认
# ---------------------------------------------------------------------------


class PendingReasonTest(unittest.TestCase):

    @staticmethod
    def _desired(**kw):
        base = {"name": "毛利率-公司口径", "type": "3", "formula": "grossProfitRate",
                "formula_status": "verified", "source_fields": ["grossProfitRate"]}
        base.update(kw)
        return gov.DesiredIndex.from_dict(base)

    def test_verified_but_missing_id_pending_reason(self):
        existing = [{"indexName": "毛利率-公司口径", "indexFormula": "profit/sales*100"}]  # 无 id
        plan = gov.build_plan(existing, [self._desired()])
        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending_missing_id, ["毛利率-公司口径"])
        self.assertEqual(plan.pending_reason(plan.pending[0]), "missing_id")
        self.assertEqual(plan.describe_lines(),
                         ["新增 0 ｜ 更新 0 ｜ 已对齐 0 ｜ 待确认公式或标识 1"])

    def test_needs_human_pending_reason(self):
        existing = []  # even with rows absent: needs_human 优先
        plan = gov.build_plan(existing, [self._desired(formula="", formula_status="needs_human")])
        self.assertEqual(plan.pending_missing_id, [])
        self.assertEqual(plan.pending_reason(plan.pending[0]), "needs_human")

    def test_replay_case5_noid_distinguishes_reason_text(self):
        raw_noid = OFFLINE / "case5" / "raw_noid"
        cfg = OFFLINE / "case5" / "config.yaml"
        if not (raw_noid / "custom_indices_1.json").exists() or not cfg.exists():
            self.skipTest("归档 case5 raw_noid 不存在")
        tmp = _sandbox(raw_src=raw_noid, files={"config.yaml": cfg})
        proc = _run_cli("--raw-dir", "raw", "--out-dir", "out", "--skill-config", "config.yaml",
                        "--keep-md", cwd=tmp)  # 断言读 .md → 按 CLI 约定加 --keep-md
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("待确认公式或标识 1", proc.stdout)
        self.assertIn("公式已人工确认，但现有指标行缺真实 indexId → 待补真实 ID 后重跑，"
                      "不能用名称充当 ID", proc.stdout)
        # 明细不得再误标 needs_human（对公式已确认的缺 ID 项）
        self.assertNotIn("以下指标公式未人工确认（needs_human），不进入提案", proc.stdout)
        report = (tmp / "raw").parent / "governance_report.md"
        self.assertIn("待补真实 indexId", report.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 4. 回读补偿记录（verify mismatch → 追加 amend，不改写历史）
# ---------------------------------------------------------------------------


def _manifest_one(index=1, tool="update_custom_report_index", target="IDX20260010",
                  name="毛利率-公司口径", expected="grossProfitRate", before="profit/sales*100"):
    return {
        "items": [{
            "index": index, "tool_id": tool, "action": "update_index", "risk": "L2",
            "target_type": "index", "target_id": target, "index_name": name,
            "params_template": {"index_name": name, "index_formula": expected, "id": target},
            "contract_version": None, "old_formula_snapshot": before,
            "expected_formula": expected, "reason": "口径不一致：与口径白皮书对齐",
        }]
    }


def _result(index=1, verdict="mismatch", actual="profit/sales*100", name="毛利率-公司口径",
            expected="grossProfitRate"):
    return {"index": index, "tool_id": "update_custom_report_index", "index_name": name,
            "expected": expected, "actual": actual, "verdict": verdict, "verdict_text": "不一致"}


def _orig_log(status="success", index=1, target="IDX20260010"):
    rec = ChangeItem(tool="update_custom_report_index", target_type="index", target_id=target,
                     field="index_formula", before="profit/sales*100", after="grossProfitRate")
    return rec.to_log_dict("tester", status) | {"index": index}


class VerifyAmendRecordTest(unittest.TestCase):

    def test_mismatch_after_success_appends_amend_only(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            log.write_text(json.dumps(_orig_log("success"), ensure_ascii=False) + "\n",
                           encoding="utf-8")
            original_line = log.read_text(encoding="utf-8")
            amends = gov.append_verify_amends(_manifest_one(), [_result()],
                                              operator="tester", log_path=str(log))
            self.assertEqual(len(amends), 1)
            amend = amends[0]
            self.assertEqual(amend["type"], "amend")
            self.assertEqual(amend["status"], "amend")  # 非 success：不被累计闸计数
            self.assertEqual(amend["status_note"], "verify mismatch（补偿记录：不改写历史；"
                                                   "累计闸仍按原始 success 计数）")
            self.assertEqual(amend["extra"]["amend_for"]["original_status"], "success")
            self.assertEqual(amend["extra"]["status_note"], "verify mismatch")
            self.assertEqual(amend["after"], "profit/sales*100")   # 回读实值
            self.assertEqual(amend["before"], "grossProfitRate")   # 期望（原记录 after）
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], original_line.strip(), "原始 success 记录不得被改写")
            self.assertEqual(json.loads(lines[1]), amend)

    def test_gate_counts_only_original_success_not_amend(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            gov.append_verify_amends(_manifest_one(), [_result()],
                                     operator="tester", log_path=str(log))
            self.assertFalse(log.exists(), "无原始 success 记录时不追加任何补偿记录")
            log.write_text(json.dumps(_orig_log("success"), ensure_ascii=False) + "\n",
                           encoding="utf-8")
            gov.append_verify_amends(_manifest_one(), [_result()],
                                     operator="tester", log_path=str(log))
            gate = AccumulatorGate(str(log))
            counted = gate._load_since(24)
            self.assertEqual(len(counted), 1, "amend 记录不得被累计闸额外计数")
            self.assertEqual(counted[0]["status"], "success")

    def test_mismatch_after_failed_no_amend(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            log.write_text(json.dumps(_orig_log("failed"), ensure_ascii=False) + "\n",
                           encoding="utf-8")
            amends = gov.append_verify_amends(_manifest_one(), [_result()],
                                              operator="tester", log_path=str(log))
            self.assertEqual(amends, [])
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_match_or_unknown_field_no_amend(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            log.write_text(json.dumps(_orig_log("success"), ensure_ascii=False) + "\n",
                           encoding="utf-8")
            for verdict in ("match", "unknown_field", "not_found"):
                amends = gov.append_verify_amends(
                    _manifest_one(), [_result(verdict=verdict, actual=None)],
                    operator="tester", log_path=str(log))
                self.assertEqual(amends, [], verdict)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_cli_verify_appends_amend_and_discloses(self):
        """复放 live case5 变体 B（record 落 success 后回读 mismatch，模拟静默失败）。"""
        case = LIVE / "case5"
        manifest = sorted(case.glob("execute_manifest_*.json"))
        if not (manifest and (case / "ad_changes.jsonl").exists()
                and (case / "raw" / "custom_indices_verify_1.json").exists()):
            self.skipTest("归档 live case5 不存在")
        tmp = _sandbox(raw_src=case / "raw", files={"metric_governance.yaml": case / "metric_governance.yaml",
                                                    "manifest.json": manifest[-1],
                                                    "ad_changes.jsonl": case / "ad_changes.jsonl"})
        target = json.loads((tmp / "manifest.json").read_text(encoding="utf-8"))["items"][0]
        # 把回读信封中目标指标的公式改回旧值 → verify 判 mismatch（写未生效形态）
        vp = tmp / "raw" / "custom_indices_verify_1.json"
        env = json.loads(vp.read_text(encoding="utf-8"))
        patched = 0
        for r in env["data"]["rows"]:
            if r.get("indexId") == target["target_id"]:
                r["indexFormula"] = target["old_formula_snapshot"]
                patched += 1
        vp.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(patched, "沙箱回读信封应含目标指标行")
        proc = _run_cli("--verify", "--manifest", "manifest.json", "--raw-dir", "raw",
                        "--out-dir", "out", "--skill-config", "metric_governance.yaml", cwd=tmp)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, combined)
        self.assertIn("❌ 不一致", proc.stdout)
        self.assertIn("已向 ad_changes.jsonl 追加 1 条补偿记录（type=amend，指向原 success 记录，"
                      "不改写历史）；累计闸按原始 success 计数的风险见记录 status_note=verify mismatch",
                      proc.stdout)
        lines = (tmp / "ad_changes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["status"], "success")  # 原记录保持 success
        amend = json.loads(lines[1])
        self.assertEqual(amend["type"], "amend")
        self.assertEqual(amend["extra"]["amend_for"]["original_status"], "success")
        verify_files = list((tmp / "out").glob("verify_*.json"))
        self.assertEqual(len(verify_files), 1)
        payload = json.loads(verify_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["results"][0]["verdict"], "mismatch")
        self.assertEqual(len(payload["amend_records"]), 1)
        self.assertEqual(payload["amend_records"][0]["extra"]["status_note"], "verify mismatch")


# ---------------------------------------------------------------------------
# 5. SKILL.md 速查表：update id 必填
# ---------------------------------------------------------------------------


class SkillDocRequiredIdTest(unittest.TestCase):

    def test_update_row_marks_id_required(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        # 速查表行（line 205 附近）：4 列 = 工具 | 必填 | 可选 | 列表字段
        rows = [l for l in text.splitlines()
                if l.startswith("| `update_custom_report_index`") and l.count("|") == 5]
        self.assertEqual(len(rows), 1)
        cols = [c.strip() for c in rows[0].strip("|").split("|")]
        self.assertEqual(cols[1].split(", "), ["`index_formula`", "`index_name`", "`type`", "`id`"])
        self.assertNotIn("`id`", cols[2].split(", "), "id 不得再列为可选")

    def test_manifest_params_always_carry_id_for_update(self):
        item = gov.manifest_change_items(_manifest_one())[0]
        manifest = gov.build_execute_manifest([item], operator="tester")
        params = manifest["items"][0]["params_template"]
        self.assertEqual(params.get("id"), "IDX20260010", "update 参数模板必须携带真实指标 id")


class V17DeFeishuLocalReportIdsTest(unittest.TestCase):
    """[v1.7.0] 去飞书（审批=会话内人工确认）+ 报表 ID 从共享 reports.yaml 缓存改为本技能 config。"""

    def test_docs_have_no_feishu_chain(self):
        for rel in ("SKILL.md", "references/workflow.md", "config/metric_governance.yaml"):
            text = (SKILL / rel).read_text(encoding="utf-8")
            self.assertNotIn("push_proposal", text, rel)
            self.assertNotIn("写飞书", text, rel)
            self.assertNotIn("ONBOARDING", text, rel)
            self.assertNotIn("_shared/config/reports.yaml", text, rel)
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("会话内", skill)
        cfg = (SKILL / "config" / "metric_governance.yaml").read_text(encoding="utf-8")
        self.assertNotIn("block_feishu_write:", cfg)
        self.assertIn("block_external_write:", cfg)

    def test_no_load_shared_reports_left(self):
        src = (SCRIPTS / "index_governor.py").read_text(encoding="utf-8")
        run_src = (SCRIPTS / "run_governance.py").read_text(encoding="utf-8")
        self.assertNotIn("load_shared_reports", src + run_src)
        for name in ("index_governor.py", "run_governance.py"):
            self.assertNotIn("飞书", (SCRIPTS / name).read_text(encoding="utf-8"), name)

    def test_target_report_ids_from_local_config(self):
        # report_id 直接维护在本技能 config 的 report_ids 映射（网页端确认后回填）。
        cfg = {"target_reports": ["daily_ops", "profit"],
               "report_ids": {"daily_ops": "123", "profit": ""}}
        ids, missing = gov.target_report_ids(cfg)
        self.assertEqual(ids, ["123"])
        self.assertEqual(missing, ["profit"])
        self.assertEqual(gov.target_report_ids({"target_reports": [], "report_ids": {}}), ([], []))

    def test_missing_report_hint_points_to_local_config(self):
        src = (SCRIPTS / "run_governance.py").read_text(encoding="utf-8")
        self.assertIn("report_ids", src)
        self.assertIn("不猜 ID", src)


if __name__ == "__main__":
    unittest.main()

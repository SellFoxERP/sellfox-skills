#!/usr/bin/env python3
"""广告数据层 —— 落盘校验 + 组装器（v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md` 与 `references/workflow.md`）：

* **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
  按「取数计划」把每步**完整信封**落盘到 `out/raw/`：

  | 落盘文件 | 工具 | 列表字段 |
  |---|---|---|
  | `campaigns_<shopId>.json`（游标多页时 `.2.json`、`.3.json`…） | `get_sp_campaign_list` | `itemList` + `nextToken` |
  | `keywords_<shopId>.json`（同上） | `get_sp_keyword_list` | `itemList` |
  | `ad_products_<shopId>.json` | `get_sp_ad_product_list` | `itemList` |
  | `targetings_<shopId>.json` | `get_sp_product_targeting_list` | `itemList` |
  | `download_tasks.json` | `get_ad_download_task_page_list` | `rows` |
  | `perf_campaign_<date>.xlsx` / `perf_target_<date>.xlsx` | agent 下载 `downloadUrl` 后放入 | — |
  | `perf_custom_<shopId>.json`（可选兜底） | `get_custom_report_page_list` | `rows` |

* **本脚本 = 计算面**：只读落盘文件——校验（存在性/status/空数据/演示指纹/截断），
  组装出与旧版**完全相同形状**的 `AdData`，`diagnose.py` 零改动。
  xlsx 由 agent 下载后已在本机，解析（openpyxl，可选依赖）也是纯本地计算；
  缺文件/缺依赖时按旧版口径降级并披露，**绝不写 0**。

本文件**不含任何网络代码**；旧版直连 MCP 的取数函数已随 v3 删除
（下载任务创建/轮询/下载均由 agent 执行，见 workflow.md「写操作 agent 执行协议」）。

用法（只读校验 + 打印就绪摘要）::

    python scripts/ad_fetch.py --shop-id 10001 --start 2026-10-08 --end 2026-10-14
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import SKILL_ROOT, load_optimizer
from _bootstrap import raw_dir as default_raw_dir
from sellfox_offline import (
    RawEnvelope,
    detect_demo_environment,
    is_no_data,
    load_envelope,
)


# ---------------------------------------------------------------------------
# 数据结构（与旧版形状一致，diagnose.py 零改动）
# ---------------------------------------------------------------------------


@dataclass
class AdData:
    """一次取数的完整结果（结构 + 绩效 + 各种披露信息）。"""

    campaigns: List[Dict[str, Any]] = field(default_factory=list)
    keywords: List[Dict[str, Any]] = field(default_factory=list)
    ad_products: List[Dict[str, Any]] = field(default_factory=list)
    product_targetings: List[Dict[str, Any]] = field(default_factory=list)
    perf_campaign: List[Dict[str, Any]] = field(default_factory=list)
    perf_keyword: List[Dict[str, Any]] = field(default_factory=list)
    perf_source: str = "none"          # download_task | custom_report | none
    perf_scope: str = "none"           # campaign | msku | none
    truncations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    demo_fingerprint: Optional[str] = None
    shop_id: str = ""
    # ---- v3 新增（仅供披露/诊断，diagnose.py 不消费）----
    raw_dir: str = ""                          # 本次组装读取的落盘目录
    perf_files: Dict[str, Any] = field(default_factory=dict)
    # {"campaign": {"path": …, "exists": bool}, "keyword": {...}}（路径与存在性）
    download_tasks: Dict[str, Any] = field(default_factory=dict)
    # download_tasks.json 信封摘要：{"exists", "status", "states": {reportState: n}}

    def disclose(self) -> str:
        """生成必须在报告中披露的「覆盖范围 + 数据来源」说明。"""
        lines: List[str] = []
        for t in self.truncations:
            lines.append(f"- {t}")
        if self.perf_source == "custom_report":
            lines.append(
                "- ⚠️ 绩效来源为自定义报表（MSKU 级），**无法归因到单个广告活动**，"
                "故活动级/关键词级诊断降级为「人工核查」。"
            )
        if self.perf_source == "none":
            lines.append("- ⚠️ 未取得绩效数据，仅能输出结构与状态诊断。")
        return "\n".join(lines) if lines else "- 数据完整覆盖，未发生截断。"


class AdFetchError(RuntimeError):
    """落盘数据组装失败（文件缺失/损坏到无法继续）。"""


# ---------------------------------------------------------------------------
# xlsx 解析（纯本地文件计算，可选依赖 openpyxl）
# ---------------------------------------------------------------------------


def parse_xlsx(path: Path) -> List[Dict[str, Any]]:
    """把本地 xlsx 解析成行字典列表（首行为表头）。

    依赖 ``openpyxl``（可选第三方）。未安装时给出明确提示，不静默失败。
    """
    try:
        import io

        from openpyxl import load_workbook  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境无 openpyxl
        raise AdFetchError(
            "解析 xlsx 需要 openpyxl：请执行 `pip install openpyxl`，"
            "或改用 data_source.perf_primary=custom_report。"
        ) from exc

    wb = load_workbook(io.BytesIO(path.read_bytes()), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []
    headers = [str(h).strip() if h is not None else "" for h in header]
    out: List[Dict[str, Any]] = []
    for values in rows_iter:
        if values is None:
            continue
        row = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
        if any(v is not None and str(v).strip() != "" for v in row.values()):
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# 落盘读取辅助（信封校验 + 游标页合并 + 硬截断披露）
# ---------------------------------------------------------------------------


def _merge_envelopes(
    paths: List[Path],
    list_key: str,
    source: str,
    max_rows: int,
    *,
    sink: "AdData",
) -> List[Dict[str, Any]]:
    """合并同一逻辑步骤的多个落盘信封（游标多页 = 多文件），带校验与披露。

    * 文件不存在 → 由调用方决定降级；
    * status=failed 的页 → 记 warning，跳过该页数据；
    * 行数超 max_rows → 硬截断并披露（与旧版客户端截断口径一致）；
    * 末页仍有 nextToken → 披露「未继续翻页」（游标 60s 过期，不追页）。
    """
    rows: List[Dict[str, Any]] = []
    for p in paths:
        try:
            env = load_envelope(p)
        except Exception as exc:
            sink.warnings.append(f"{source}：落盘文件无法解析（{p.name}：{exc}），该页跳过。")
            continue
        sink.warnings.extend(f"{source}: {w}" for w in env.warnings)
        if env.status not in ("complete", "partial"):
            sink.warnings.append(
                f"{source}：{p.name} status=failed error_code={env.error_code}，该页跳过。"
            )
            continue
        if env.partial:
            sink.warnings.append(f"{source}：{p.name} status=partial，可采信但须在数据说明披露。")
        page = env.rows(list_key) if isinstance(env.data, dict) else []
        rows.extend(r for r in page if isinstance(r, dict))
        if env.coverage.get("truncated"):
            sink.truncations.append(
                f"⚠️ {source}（{p.name}）：网关 coverage 标记截断"
                f"（fetched={env.coverage.get('fetched_count')}/total={env.coverage.get('total_count')}）。"
            )
        # 游标未尽：披露而非追页（计算面零网络，游标 60s 过期也来不及）
        if isinstance(env.data, dict) and env.data.get("nextToken"):
            sink.truncations.append(
                f"⚠️ {source}（{p.name}）：信封仍含 nextToken（后续页未取或未落盘），"
                "统计仅覆盖已落盘页。"
            )
    if max_rows and len(rows) > max_rows:
        sink.truncations.append(
            f"⚠️ {source}：落盘 {len(rows)} 行，超 max_rows={max_rows}，"
            f"仅保留前 {max_rows} 行，其余未纳入诊断。"
        )
        rows = rows[:max_rows]
    return rows


def _structure(raw: Path, base: str, shop_id: str, list_key: str, max_rows: int,
               sink: "AdData", *, required: bool) -> List[Dict[str, Any]]:
    """读取 `<base>_<shopId>*.json`（含游标多页 `.2.json` 等），返回行列表。"""
    paths = sorted(raw.glob(f"{base}_{shop_id}*.json"))
    if not paths:
        msg = f"落盘缺失 out/raw/{base}_{shop_id}.json（取数计划步骤 {base}）。"
        if required:
            raise AdFetchError(msg)
        sink.warnings.append(msg + "对应诊断维度跳过。")
        return []
    return _merge_envelopes(paths, list_key, base, max_rows, sink=sink)


def _classify_perf_xlsx(raw: Path) -> Dict[str, List[Path]]:
    """把 out/raw/perf*.xlsx 按文件名归类为 campaign / keyword 两个 scope。

    约定（见 workflow.md 取数计划）：
      * `perf_campaign_<date>.xlsx` → 活动级绩效（adCampaignReport）；
      * `perf_target_<date>.xlsx`（或 perf_keyword_*）→ 关键词/投放级（adTargetReport）；
      * 兜底：只有一个无标记的 `perf_<date>.xlsx` 时按 campaign 处理并记 warning。
    """
    out: Dict[str, List[Path]] = {"campaign": [], "keyword": []}
    for p in sorted(raw.glob("perf*.xlsx")):
        n = p.name.lower()
        if "campaign" in n:
            out["campaign"].append(p)
        elif "target" in n or "keyword" in n:
            out["keyword"].append(p)
        elif not (out["campaign"] or out["keyword"]):
            out["campaign"].append(p)
    return out


def _report_state_class(state: Any) -> str:
    """把 reportState 归一化为 success / failed / unknown（中英文都认）。

    实测 reportState 取值为中文（已生成 / 生成失败 / 生成中…），旧判定只认
    英文 FAIL/ERROR 子串，中文失败态漏报告警（评测 2026-10-16 #5）。口径：
      * failed ：含「失败」，或含 FAIL/ERROR 子串（生成失败、失败、FAILED…）；
      * success：已生成 / 生成完成，或 SUCCESS/COMPLETED/GENERATED 整词；
      * 其余（生成中、排队中…）= unknown：不是失败，也不算就绪。
    """
    s = str(state or "").strip()
    if not s:
        return "unknown"
    up = s.upper()
    if "失败" in s or "FAIL" in up or "ERROR" in up:
        return "failed"
    if "已生成" in s or "生成完成" in s or up in ("SUCCESS", "SUCCESSFUL", "COMPLETED", "GENERATED"):
        return "success"
    return "unknown"


def _load_download_tasks(raw: Path, sink: "AdData") -> None:
    """读取 download_tasks.json（get_ad_download_task_page_list 信封），记录任务态摘要。"""
    sink.download_tasks = {"exists": False, "status": "", "states": {}}
    p = raw / "download_tasks.json"
    if not p.exists():
        sink.warnings.append(
            "落盘缺失 out/raw/download_tasks.json（下载任务进度信封）；"
            "绩效 xlsx 的任务来源无法核对。"
        )
        return
    try:
        env = load_envelope(p)
    except Exception as exc:
        sink.warnings.append(f"download_tasks.json 无法解析：{exc}。")
        return
    sink.download_tasks["exists"] = True
    sink.download_tasks["status"] = env.status
    states: Dict[str, int] = {}
    for row in env.rows("rows"):
        if isinstance(row, dict):
            key = str(row.get("reportState") or "unknown")
            states[key] = states.get(key, 0) + 1
    sink.download_tasks["states"] = states
    failed_states = {k: n for k, n in states.items() if _report_state_class(k) == "failed"}
    if failed_states:
        sink.warnings.append(
            f"下载任务存在失败态：{failed_states}，对应绩效文件不可信（成功态为「已生成」）。"
        )


# ---------------------------------------------------------------------------
# 组装入口（校验 + 组装，输出与旧版 fetch_all 同形状的 AdData）
# ---------------------------------------------------------------------------


def load_from_raw(
    raw: Path,
    shop_id: str,
    start_date: str,
    end_date: str,
    opt_cfg: Dict[str, Any],
    *,
    with_structure: bool = True,
    with_perf: bool = True,
) -> AdData:
    """从 agent 落盘的 out/raw/ 组装 AdData（零网络；diagnose.py 零改动）。

    :param raw: 落盘目录（一般 `<skill>/out/raw`）。
    :param shop_id: 店铺 ID（决定 `campaigns_<shopId>.json` 等文件名）。
    :param start_date/end_date: 绩效窗口（yyyy-MM-dd），用于披露与核对。
    :param opt_cfg: `config/optimizer.yaml` 内容（scope / data_source）。
    """
    scope = opt_cfg.get("scope") or {}
    max_campaigns = int(scope.get("max_campaigns") or 100)
    max_keywords = int(scope.get("max_keywords") or 200)
    max_perf = int(scope.get("max_perf_rows") or 500)

    data = AdData(shop_id=shop_id or "(未指定)", raw_dir=str(raw))
    if not shop_id:
        data.warnings.append("未提供 shop_id：结构落盘（campaigns_<shopId>.json 等）将匹配不到。")

    if with_structure:
        data.campaigns = _structure(raw, "campaigns", shop_id, "itemList", max_campaigns,
                                    data, required=True)
        data.keywords = _structure(raw, "keywords", shop_id, "itemList", max_keywords,
                                   data, required=False)
        data.ad_products = _structure(raw, "ad_products", shop_id, "itemList", max_keywords,
                                      data, required=False)
        data.product_targetings = _structure(raw, "targetings", shop_id, "itemList", max_keywords,
                                             data, required=False)

    if with_perf:
        _load_download_tasks(raw, data)
        primary = str((opt_cfg.get("data_source") or {}).get("perf_primary") or "none")
        xlsx = _classify_perf_xlsx(raw) if primary == "download_task" else {"campaign": [], "keyword": []}
        data.perf_files = {
            scope_: {"path": str(ps[0]) if ps else "", "exists": bool(ps)}
            for scope_, ps in xlsx.items()
        }
        if primary == "download_task":
            got_any = False
            xlsx_present = bool(xlsx["campaign"] or xlsx["keyword"])
            for scope_, paths in xlsx.items():
                for p in paths:
                    try:
                        rows = parse_xlsx(p)[:max_perf]
                    except AdFetchError as exc:
                        data.warnings.append(str(exc))
                        continue
                    if scope_ == "campaign":
                        data.perf_campaign = rows
                    else:
                        data.perf_keyword = rows
                    got_any = True
            if got_any:
                data.perf_source = "download_task"
                data.perf_scope = "campaign"
            elif xlsx_present:
                data.warnings.append(
                    "绩效 xlsx 已落盘但解析失败（见上），绩效数据不可用。"
                )
            else:
                data.warnings.append(
                    f"绩效 xlsx 落盘缺失（out/raw/perf_campaign_{end_date}.xlsx 等，窗口 "
                    f"{start_date}~{end_date}）：请由 agent 按 workflow.md 取数计划下载任务链取回。"
                )
        elif primary == "custom_report":
            p = raw / f"perf_custom_{shop_id}.json"
            if not p.exists():
                data.warnings.append(
                    f"落盘缺失 out/raw/perf_custom_{shop_id}.json（custom_report 兜底），跳过绩效取数。"
                )
            else:
                rows = _merge_envelopes([p], "rows", "perf_custom", max_perf, sink=data)
                data.perf_campaign = rows
                data.perf_source = "custom_report"
                data.perf_scope = "msku"
        else:
            data.warnings.append("data_source.perf_primary=none，跳过绩效取数。")

    # 演示环境识别（铁律 11：跨全部落盘内容扫描）
    payload = {
        "campaigns": data.campaigns,
        "keywords": data.keywords,
        "perf_campaign": data.perf_campaign,
        "perf_keyword": data.perf_keyword,
        "download_tasks": data.download_tasks,
    }
    data.demo_fingerprint = detect_demo_environment(payload)
    return data


# ---------------------------------------------------------------------------
# CLI：落盘就绪校验（诊断前 agent/人可单独跑）
# ---------------------------------------------------------------------------


def _readiness_summary(data: AdData) -> str:
    dl = data.download_tasks or {}
    perf = data.perf_files or {}
    lines = [
        f"raw_dir = {data.raw_dir}",
        f"campaigns={len(data.campaigns)} keywords={len(data.keywords)} "
        f"ad_products={len(data.ad_products)} targetings={len(data.product_targetings)}",
        f"perf: source={data.perf_source} scope={data.perf_scope} "
        + " ".join(f"{k}({'有' if v.get('exists') else '缺'})" for k, v in perf.items())
        + (f" | download_tasks(exists={dl.get('exists')}, states={dl.get('states')})"
           if dl else ""),
        f"demo_fingerprint={data.demo_fingerprint or '无'}",
    ]
    for t in data.truncations:
        lines.append(f"⚠️ 截断/覆盖披露：{t}")
    for w in data.warnings:
        lines.append(f"⚠️ 降级/告警：{w}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="广告落盘数据校验 + 组装就绪摘要（零网络）")
    ap.add_argument("--shop-id", required=True)
    ap.add_argument("--start", required=True, help="yyyy-MM-dd（绩效窗口起）")
    ap.add_argument("--end", required=True, help="yyyy-MM-dd（绩效窗口止）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    args = ap.parse_args(argv)

    raw = Path(args.raw_dir).expanduser() if args.raw_dir else default_raw_dir()
    if not raw.is_absolute():
        raw = SKILL_ROOT / raw
    try:
        data = load_from_raw(raw, args.shop_id, args.start, args.end, load_optimizer())
    except AdFetchError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        print("   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」"
              "调用 mcp__Sellfox-MCP__{search,action} 并落盘到 out/raw/，再重跑。", file=sys.stderr)
        return 2
    print(_readiness_summary(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""落盘数据校验器（v3 计算面共享组件）—— 校验 agent 落盘的 out/raw/*.json，零网络。

用法（各技能的 fetch_*.py 校验模式统一走这里）：

    python validate_raw.py --dir <skill>/out/raw \
        --step shops:rows --step profit_shop:reportList \
        [--window 2026-10-14:2026-10-14] [--json]

    --step <文件名[:列表字段]>   期望存在的信封及其列表字段（rows/reportList/itemList）
    --window start:end          业务日窗口（yyyy-MM-dd），用于「演示环境日期过滤不可信」检测
    --json                      输出机器可读 JSON（否则输出人类可读摘要）

退出码：0 = 全部就绪；2 = 有缺失/失败步骤（技能可据此走降级路径）。

设计约定见 _shared/SELLFOX_CLIENT.md §7（落盘约定）。
仅依赖 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 定位 _shared（本脚本就在 _shared/scripts/ 下，父目录即 _shared）
_SHARED_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED_ROOT / "scripts"))

from sellfox_offline import (  # noqa: E402
    RawEnvelope,
    detect_date_filter_unreliable,
    detect_demo_environment,
    is_no_data,
    load_envelope,
)


def validate_step(
    raw_dir: Path,
    step: str,
    window: Optional[tuple],
) -> Dict[str, Any]:
    """校验单个步骤的落盘文件，返回结论字典。"""
    name, _, list_key = step.partition(":")
    list_key = list_key or None
    result: Dict[str, Any] = {"step": name, "expected_list_key": list_key}

    path = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not path.exists():
        result.update(state="missing", verdict=f"缺少落盘文件 {path}")
        return result

    try:
        env = load_envelope(path)
    except Exception as exc:  # JSON 损坏
        result.update(state="corrupt", verdict=f"文件无法解析：{exc}")
        return result

    result["summary"] = env.summary()
    result["warnings"] = env.warnings
    result["output_validation"] = env.obj.get("output_validation")

    # 1) 状态
    if env.status not in ("complete", "partial"):
        result.update(
            state="failed",
            verdict=f"status={env.status} error_code={env.error_code} {env.message or ''}",
        )
        return result

    # 2) 空数据
    if is_no_data(env.data):
        result.update(state="no_data", verdict="无数据（标「无数据」，不写 0，不重试）")
        return result

    # 3) partial 披露
    if env.partial:
        result.update(state="partial", verdict="部分成功：可采信但必须在数据说明披露")

    # 4) 列表字段
    if list_key and isinstance(env.data, dict) and not isinstance(env.data.get(list_key), list):
        result.update(
            state="bad_shape",
            verdict=f"期望列表字段 {list_key} 不存在（实际 data 键：{list(env.data.keys())[:8]}）",
        )
        return result

    # 5) 演示指纹
    demo = detect_demo_environment(env.obj)
    if demo:
        result["demo_fingerprint"] = demo

    # 6) 日期窗口可靠性（演示环境常见病）
    if window:
        if detect_date_filter_unreliable(env.data, window[0], window[1]):
            result["date_filter_unreliable"] = True

    # 7) 截断披露
    if env.coverage.get("truncated"):
        result["truncated"] = True

    result.setdefault(
        "state",
        "ready" if not env.partial else "partial",
    )
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="校验 agent 落盘的 out/raw/*.json")
    ap.add_argument("--dir", required=True, help="out/raw 目录")
    ap.add_argument(
        "--step", action="append", default=[],
        help="期望步骤 文件名[:列表字段]，如 shops:rows / profit_shop:reportList",
    )
    ap.add_argument("--window", default=None, help="业务日窗口 start:end（yyyy-MM-dd）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    raw_dir = Path(args.dir).expanduser().resolve()
    window = None
    if args.window:
        s, _, e = args.window.partition(":")
        window = (s, e)

    results = [validate_step(raw_dir, s, window) for s in args.step]
    bad_states = {"missing", "corrupt", "failed", "bad_shape"}
    ok = all(r["state"] not in bad_states for r in results)
    demo_hit = any("demo_fingerprint" in r for r in results)

    payload = {
        "raw_dir": str(raw_dir),
        "window": args.window,
        "ready": ok,
        "demo_environment": demo_hit,
        "steps": results,
        "notes": [],
    }
    if demo_hit:
        payload["notes"].append("检测到演示环境指纹：输出须加「⚠️ 演示数据」水印并默认阻断写入")
    if any(r.get("date_filter_unreliable") for r in results):
        payload["notes"].append("演示环境日期过滤不可信：数据说明须声明数值仅验证连通性")
    if any(r.get("truncated") for r in results):
        payload["notes"].append("存在 max_rows 截断：须披露「仅覆盖 Top N，其余未纳入」")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = {"ready": "✅", "partial": "🟡", "no_data": "⬜"}.get(r["state"], "❌")
            print(f"{mark} {r['step']}: {r['state']} — {r.get('verdict') or r.get('summary', '')}")
            for extra in ("demo_fingerprint", "date_filter_unreliable", "truncated"):
                if r.get(extra):
                    print(f"     · {extra}={r[extra]}")
        for note in payload["notes"]:
            print(f"⚠️ {note}")
        print("结论：数据就绪" if ok else "结论：存在缺失/失败步骤，按取数计划走降级路径")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

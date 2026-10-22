"""赛狐离线工具（v3 计算面共享组件）—— 只处理落盘数据，零网络。

从旧 sellfox_client.py 拆出的离线部分 + v3 新增的信封/演示识别工具。
取数面（agent 调 mcp__Sellfox-MCP__*）的调用纪律见 _shared/SELLFOX_CLIENT.md。

仅依赖 Python 标准库。
"""

from __future__ import annotations

import calendar
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 演示环境指纹（铁律 11 扩充版，2026-10-15 实测补充）
# ---------------------------------------------------------------------------

DEMO_FINGERPRINTS: Tuple[str, ...] = (
    "MOCK-",
    "mock.example.com",
    "demo.example.com",
    "SHOP-DEMO-",
    "B0DEMO",
    "DPARENT",
    "演示",
)

#: 信封里常见的日期字段名（用于「日期过滤不可信」检测）。
DATE_FIELDS: Tuple[str, ...] = ("day", "date", "statDate", "stat_date", "bizDay")


def iter_values(obj: Any) -> Iterable[str]:
    """深度遍历任意嵌套结构，产出其中的字符串叶子值。"""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from iter_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from iter_values(v)
    elif isinstance(obj, str):
        yield obj


def detect_demo_environment(payload: Any) -> Optional[str]:
    """检测演示/沙箱环境指纹。

    命中任一 :data:`DEMO_FINGERPRINTS` 即返回该指纹，否则返回 ``None``。
    技能应在输出顶部加水印，并默认阻断写飞书/写操作。
    """
    for value in iter_values(payload):
        for fp in DEMO_FINGERPRINTS:
            if fp in value:
                return fp
    return None


def detect_date_filter_unreliable(
    payload: Any, window_start: str, window_end: str
) -> bool:
    """检测「演示环境日期过滤失效」：返回数据的日期字段不在请求窗口内。

    演示/沙箱后端会忽略日期过滤（实测请求 2026-10-14 返回 2024-01 数据）。
    只要有任一日期字段值落在 ``[window_start, window_end]``（yyyy-MM-dd 字符串比较）之外，
    即判定不可信。窗口也支持月粒度（``yyyyMM`` / ``yyyy-MM``，自动扩展为当月首末日）。
    数据无日期字段时返回 False（无法判定）。
    """
    def normalized(value):
        text = str(value or "")
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        return None

    start, end = normalized(window_start), normalized(window_end)
    if start is None:
        start = _month_bounds(window_start)[0]
    if end is None:
        end = _month_bounds(window_end)[1]
    if not start or not end:
        return False
    for row in _iter_rows(payload):
        if not isinstance(row, dict):
            continue
        for field in DATE_FIELDS:
            day = normalized(row.get(field))
            if day and not (start <= day <= end):
                return True
    return False


def _month_bounds(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """月粒度窗口（``yyyyMM`` / ``yyyy-MM``）扩展为当月首末日，供日值比较。

    返回 ``(首日, 末日)``（yyyy-MM-dd）；非月格式或非法月份返回 ``(None, None)``。
    """
    text = str(value or "").strip()
    year = month = None
    if len(text) == 6 and text.isdigit():
        year, month = int(text[:4]), int(text[4:6])
    elif len(text) == 7 and text[4] == "-":
        year, month = int(text[:4]), int(text[5:7])
    if year is None or month is None or not (1 <= month <= 12):
        return None, None
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _iter_rows(payload: Any) -> Iterable[Any]:
    """从信封/数据结构中粗粒度找出「行」：列表字段或嵌套列表。"""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in ("rows", "reportList", "itemList") and isinstance(v, list):
                yield from v
            else:
                yield from _iter_rows(v)
    elif isinstance(payload, list):
        yield from payload


# ---------------------------------------------------------------------------
# 空数据 / 信封解析
# ---------------------------------------------------------------------------


def is_no_data(data: Any) -> bool:
    """判断业务 data 是否为「无数据」（None / {} / []）。

    空数据应标注「无数据」，**不写 0**（0 和「没拿到」是两回事）。
    """
    return data is None or data == {} or data == []


class RawEnvelope:
    """一个落盘的 out/raw/*.json 信封的解析结果。"""

    def __init__(self, path: Path, obj: Dict[str, Any]):
        self.path = path
        self.obj = obj
        self.status: str = obj.get("status", "")
        self.data: Any = obj.get("data") if self.status in ("complete", "partial") else None
        # 原始响应保留在 obj；失败/未知状态的值不可进入经营计算。
        self.coverage: Dict[str, Any] = obj.get("coverage") or {}
        self.pagination: Dict[str, Any] = obj.get("pagination") or {}
        self.warnings: List[str] = list(obj.get("warnings") or [])
        if self.status not in ("complete", "partial"):
            self.warnings.append(f"响应状态 {self.status or '缺失'} 不可采信，需人工核查")
        self.period: Dict[str, Any] = obj.get("period") or {}
        self.error_code: Optional[str] = obj.get("error_code")
        self.message: Optional[str] = obj.get("message")

    @property
    def ok(self) -> bool:
        """status complete 且非空数据。"""
        return self.status == "complete" and not is_no_data(self.data)

    @property
    def partial(self) -> bool:
        return self.status == "partial"

    def rows(self, list_key: Optional[str] = None) -> List[Any]:
        """取出行列表（list_key 缺省按 rows/reportList/itemList 自动识别）。"""
        if not isinstance(self.data, dict):
            return []
        for key in ([list_key] if list_key else ("rows", "reportList", "itemList")):
            v = self.data.get(key)
            if isinstance(v, list):
                return v
        return []

    def summary(self) -> str:
        """一句话数据说明（可直接进报告）。"""
        parts = [f"{self.path.name}: status={self.status}"]
        n = len(self.rows())
        if n:
            parts.append(f"rows={n}")
        cov = self.coverage
        if cov:
            parts.append(
                f"coverage={'完整' if cov.get('complete') else '不完整'}"
                f"({cov.get('fetched_count')}/{cov.get('total_count')})"
            )
        if cov.get("truncated"):
            parts.append("⚠️ 达 max_rows 截断")
        if is_no_data(self.data):
            parts.append("无数据")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        return " | ".join(parts)


def load_envelope(path: Path) -> RawEnvelope:
    """读取并解析一个落盘信封文件。"""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path} 不是 JSON 对象")
    return RawEnvelope(Path(path), obj)


# ---------------------------------------------------------------------------
# 自检：python sellfox_offline.py
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover
    demo = {"rows": [{"shopId": "SHOP-DEMO-1001", "shopName": "演示店"}]}
    print("演示指纹:", detect_demo_environment(demo))
    print("无数据:", is_no_data({}), is_no_data(None), is_no_data({"rows": []}))
    dates = {"rows": [{"day": "2024-01-01"}]}
    print(
        "日期过滤不可信:",
        detect_date_filter_unreliable(dates, "2026-10-01", "2026-10-14"),
    )

# ---------------------------------------------------------------------------
# PII 脱敏（v1.7.0 自 feishu_writer 迁入；飞书能力已整体移除）
# ---------------------------------------------------------------------------

def mask_email(value: str) -> str:
    """掩码邮箱：``ab***@example.com``。"""
    if not value or "@" not in value:
        return "***"
    local, _, domain = value.partition("@")
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


def mask_name(value: str) -> str:
    """掩码姓名：保留首字符，其余用 ``*`` 代替。"""
    if not value:
        return "***"
    if len(value) == 1:
        return value + "*"
    return value[0] + "*" * (len(value) - 1)


def mask_pii(value: Any) -> Any:
    """递归脱敏任意结构中的 PII 字段（``buyerEmail`` / ``buyerName`` 等）。

    写入飞书或任何外部文档前**必须**先过这一层。
    返回新对象，不修改入参。
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if k in ("buyerEmail", "buyer_email"):
                out[k] = mask_email(str(v))
            elif k in ("buyerName", "buyer_name"):
                out[k] = mask_name(str(v))
            elif k in ("buyerAddress", "buyer_address"):
                out[k] = "***"
            else:
                out[k] = mask_pii(v)
        return out
    if isinstance(value, list):
        return [mask_pii(v) for v in value]
    return value


def mask_pii_in_text(text: str) -> str:
    """对已渲染的文本做行级 PII 兜底脱敏（防漏网的 ``buyerEmail`` / ``buyerName``）。

    支持 ``buyerEmail: xxx@yyy`` / ``buyerEmail=xxx@yyy`` 形态。
    邮箱值按非空白 token 捕获（邮箱不含空格）；姓名值捕获到行尾/分隔符为止
    （含中间空格），避免英文姓名只掩名不掩姓。先掩邮箱再掩姓名。
    """
    email_pattern = re.compile(
        r"(buyerEmail|buyer_email)\s*[:=]\s*([^\s,;，；]+)", re.IGNORECASE
    )
    name_pattern = re.compile(
        r"(buyerName|buyer_name)\s*[:=]\s*([^\n,;，；]+?)(?=\s*$|[,;，；])",
        re.IGNORECASE | re.MULTILINE,
    )

    text = email_pattern.sub(
        lambda m: f"{m.group(1)}: {mask_email(m.group(2))}", text
    )
    return name_pattern.sub(
        lambda m: f"{m.group(1)}: {mask_name(m.group(2).strip())}", text
    )


def guard_demo(payload: Any, *, override: bool = False) -> Optional[str]:
    """演示环境外部写出闸门（原飞书写入闸，v1.7.0 起为通用外部出口闸）。

    命中演示指纹时：默认抛 :class:`RuntimeError` 阻断外部写出；``override=True``
    时仅返回指纹并放行（但输出仍须加水印）。

    :returns: 命中的指纹（无则 ``None``）。
    """
    fingerprint = detect_demo_environment(payload)
    if fingerprint and not override:
        raise RuntimeError(
            f"⚠️ 检测到演示环境指纹「{fingerprint}」，已默认阻断外部写出。"
            "确需写出请显式传 override=True，并在输出顶部加水印。"
        )
    return fingerprint

"""飞书（Feishu / Lark）文档写入封装 —— _shared 共享底座。

仅依赖标准库（``urllib`` / ``json`` / ``time`` / ``threading``）。

职责：
  * ``tenant_access_token`` 获取与缓存（有效期约 2h，提前 60s 过期）。
  * ``create_doc`` —— 创建文档，返回 ``document_id``。
  * ``write_blocks`` —— 向文档追加 block。
  * ``markdown_to_blocks`` —— Markdown → 飞书 block 结构转换（技能主要入口）。
  * **PII 脱敏**（铁律 8）：写入外部文档前剔除/掩码 ``buyerEmail`` / ``buyerName``。
  * **演示环境阻断**（铁律 11）：检测到演示数据时默认拒绝写入，可显式覆盖。

飞书 Docx API 已知坑：
  1. 需**自建应用**，用 ``tenant_access_token`` 鉴权。
  2. **写入前必须把应用添加为文档协作者**，否则返回 403。
  3. 创建文档接口限流 **3 次/秒**（错误码 ``99991400``），同文件夹创建必须串行。
  4. 创建文档后立即写入可能遇到 block 索引问题 → 先建文档 → 追加 block → 校验。

示例::

    from feishu_writer import FeishuWriter

    fw = FeishuWriter(app_id="cli_xxx", app_secret="xxx", folder_token="fldcnXXX")
    doc_id = fw.create_doc("每日经营日报 2026-09-29")
    fw.write_blocks(doc_id, fw.markdown_to_blocks(report_markdown))
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

FEISHU_BASE = "https://open.feishu.cn/open-apis"

#: 飞书文档创建限流：3 次/秒。
CREATE_DOC_MIN_INTERVAL = 1.0 / 3.0
#: 限流错误码。
RATE_LIMIT_CODE = 99991400

#: PII 字段名（出现在订单等响应里）
PII_KEYS: Sequence[str] = ("buyerEmail", "buyerName", "buyerAddress", "buyer_address", "buyer_email", "buyer_name")


class FeishuError(RuntimeError):
    """飞书 API 调用失败。"""

    def __init__(self, message: str, *, code: Optional[int] = None, raw: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.raw = raw


# ---------------------------------------------------------------------------
# PII 脱敏
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


# ---------------------------------------------------------------------------
# Markdown → 飞书 Block
# ---------------------------------------------------------------------------

#: Markdown 行 → 飞书 block_type 映射（block_type / 内容键）
_HEADING_TYPES = {1: (3, "heading1"), 2: (4, "heading2"), 3: (5, "heading3"), 4: (6, "heading4")}


def _text_block(block_type: int, key: str, content: str) -> Dict[str, Any]:
    """构造单行文本类 block（标题/正文/列表项等）。"""
    return {
        "block_type": block_type,
        key: {"elements": [{"text_run": {"content": content}}]},
    }


class FeishuWriter:
    """飞书文档写入器（tenant_access_token 缓存 + 串行限流 + 默认不公开分享）。

    :param app_id: 自建应用 App ID（缺省读 ``FEISHU_APP_ID``）。
    :param app_secret: 自建应用 App Secret（缺省读 ``FEISHU_APP_SECRET``）。
    :param folder_token: 目标文件夹 token（可选）。
    :param blocking_demo: 检测到演示数据时是否阻断写入（默认 ``True``）。
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        *,
        folder_token: Optional[str] = None,
        blocking_demo: bool = True,
    ) -> None:
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        if not self.app_id or not self.app_secret:
            raise FeishuError(
                "缺少飞书应用凭证：请传入 app_id/app_secret 或设置 "
                "FEISHU_APP_ID / FEISHU_APP_SECRET。"
            )
        self.folder_token = folder_token or os.environ.get("FEISHU_FOLDER_TOKEN")
        self.blocking_demo = blocking_demo

        self._token: Optional[str] = None
        self._token_expire_at: float = 0.0
        # 分开两把锁：token 刷新锁 与 建文档限流锁（避免嵌套同一把非可重入锁导致死锁）。
        self._token_lock = threading.Lock()
        self._create_lock = threading.Lock()
        self._last_create_at: float = 0.0
        self._last_demo_hit: Optional[str] = None

    # -- token ------------------------------------------------------------

    def _tenant_token(self) -> str:
        """获取并缓存 ``tenant_access_token``（有效期约 2h，提前 60s 刷新）。"""
        with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_expire_at:
                return self._token

            payload = {"app_id": self.app_id, "app_secret": self.app_secret}
            resp = self._request(
                "POST",
                f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                payload,
                token=None,
            )
            token = resp.get("tenant_access_token")
            if not token:
                raise FeishuError(
                    f"获取 tenant_access_token 失败：{resp.get('msg')}",
                    code=resp.get("code"),
                )
            expire = int(resp.get("expire", 7200))
            self._token = token
            self._token_expire_at = now + max(expire - 60, 60)
            return token

    # -- 文档 -------------------------------------------------------------

    def create_doc(self, title: str, folder_token: Optional[str] = None) -> str:
        """创建飞书文档，返回 ``document_id``。

        ⚠️ 创建接口限流 3 次/秒（错误码 ``99991400``）—— 内部已串行 + 重试。
        ⚠️ 写入前必须把应用添加为文档协作者，否则后续写入返回 403。
        """
        # 同文件夹创建必须串行；这里做进程内节流。
        with self._create_lock:
            elapsed = time.monotonic() - self._last_create_at
            if elapsed < CREATE_DOC_MIN_INTERVAL:
                time.sleep(CREATE_DOC_MIN_INTERVAL - elapsed)
            payload: Dict[str, Any] = {"title": title}
            fld = folder_token or self.folder_token
            if fld:
                payload["folder_token"] = fld
            token = self._tenant_token()  # 使用独立的 _token_lock，避免嵌套死锁
            resp = self._retry_request(
                "POST", f"{FEISHU_BASE}/docx/v1/documents", payload, token=token
            )
            self._last_create_at = time.monotonic()

        doc = (resp.get("data") or {}).get("document") or {}
        doc_id = doc.get("document_id")
        if not doc_id:
            raise FeishuError(f"创建文档失败：{resp.get('msg')}", code=resp.get("code"))
        return str(doc_id)

    def write_blocks(self, document_id: str, blocks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """向文档追加 block（自动分批，每批 ≤ 50）。

        :param document_id: 目标文档 ID。
        :param blocks: :meth:`markdown_to_blocks` 或手工构造的 block 列表。
        """
        if not blocks:
            return {"code": 0, "msg": "no blocks"}

        token = self._tenant_token()
        last: Dict[str, Any] = {}
        for i in range(0, len(blocks), 50):
            batch = list(blocks[i : i + 50])
            payload = {"children": batch, "index": -1}
            url = (
                f"{FEISHU_BASE}/docx/v1/documents/{document_id}"
                f"/blocks/{document_id}/children?document_revision_id=-1"
            )
            last = self._retry_request("POST", url, payload, token=token)
            if last.get("code") not in (0, None):
                raise FeishuError(
                    f"写入 block 失败：{last.get('msg')}", code=last.get("code")
                )
        return last

    # -- Markdown → blocks ------------------------------------------------

    def markdown_to_blocks(self, markdown: str) -> List[Dict[str, Any]]:
        """把 Markdown 转换为飞书 block 列表。

        支持：标题（``#``~``####``）、无序列表（``-``/``*``）、有序列表、引用（``>``）、
        分割线（``---``）、代码块（三反引号）、普通段落。行内 ``**加粗**`` / 反引号
        会剥离标记保留文本（保持实现简单、结果稳定）。

        注意：PII 脱敏在**剥离 Markdown 标记之后**逐行执行，避免掩码用的 ``*``
        被 Markdown 强调语法误删。
        """
        text = markdown  # 先不脱敏，见下面 clean() 的说明
        blocks: List[Dict[str, Any]] = []
        in_code = False
        code_lines: List[str] = []

        def clean(s: str) -> str:
            """剥离行内标记后再脱敏（顺序很重要）。"""
            return mask_pii_in_text(_strip_inline(s))

        for raw_line in text.splitlines():
            line = raw_line.rstrip()

            if line.strip().startswith("```"):
                if in_code:
                    blocks.append(
                        _text_block(14, "code", mask_pii_in_text("\n".join(code_lines)))
                    )
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_lines.append(raw_line)
                continue

            stripped = line.strip()
            if not stripped:
                continue

            if set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:
                blocks.append({"block_type": 22, "divider": {}})
                continue

            heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
            if heading:
                level = len(heading.group(1))
                btype, key = _HEADING_TYPES[level]
                blocks.append(_text_block(btype, key, clean(heading.group(2))))
                continue

            if stripped.startswith(("- ", "* ")):
                blocks.append(_text_block(12, "bullet", clean(stripped[2:])))
                continue

            ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
            if ordered:
                blocks.append(_text_block(13, "ordered", clean(ordered.group(1))))
                continue

            if stripped.startswith(">"):
                blocks.append(_text_block(15, "quote", clean(stripped[1:].strip())))
                continue

            blocks.append(_text_block(2, "text", clean(stripped)))

        if in_code and code_lines:
            blocks.append(_text_block(14, "code", mask_pii_in_text("\n".join(code_lines))))
        return blocks

    # -- 演示环境闸门 -----------------------------------------------------

    def guard_demo(self, payload: Any, *, override: bool = False) -> Optional[str]:
        """演示环境写入闸门。

        命中演示指纹时：默认抛 :class:`FeishuError` 阻断写入；``override=True`` 时
        仅记录指纹并放行（但仍应加输出水印）。

        :returns: 命中的指纹（无则 ``None``）。
        """
        # 延迟导入（v3：离线工具在 sellfox_offline，旧 sellfox_client 已归档 legacy/）。
        from sellfox_offline import detect_demo_environment

        fingerprint = detect_demo_environment(payload)
        self._last_demo_hit = fingerprint
        if fingerprint and not override:
            raise FeishuError(
                f"⚠️ 检测到演示环境指纹「{fingerprint}」，已默认阻断写入飞书。"
                "确需写入请显式传 override=True，并在文档顶部加水印。"
            )
        return fingerprint

    # -- 内部 HTTP --------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        *,
        token: Optional[str],
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """发送一次飞书 API 请求，返回解析后的 JSON。"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"  # 飞书用 Bearer，赛狐用 X-MCP-Key
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise FeishuError(f"飞书 HTTP {exc.code}：{detail[:300]}", raw=detail[:500]) from exc
        except urllib.error.URLError as exc:
            raise FeishuError(f"飞书网络错误：{exc.reason}") from exc

        try:
            return json.loads(body)
        except ValueError as exc:
            raise FeishuError(f"飞书响应非 JSON：{body[:200]}", raw=body[:500]) from exc

    def _retry_request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        *,
        token: Optional[str],
        retries: int = 3,
    ) -> Dict[str, Any]:
        """带限流退避重试的请求（错误码 ``99991400``）。"""
        last: Dict[str, Any] = {}
        for attempt in range(retries + 1):
            last = self._request(method, url, payload, token=token)
            if last.get("code") == RATE_LIMIT_CODE and attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            break
        return last


def _strip_inline(text: str) -> str:
    """剥离行内 Markdown 标记（``**`` / ``*`` / 反引号），保留纯文本。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((.*?)\)", r"\1", text)
    return text.strip()


if __name__ == "__main__":  # pragma: no cover
    demo_md = "## 标题\n\n- 项目一\n- 项目二\n\nbuyerEmail: abc@example.com\nbuyerName: 张三\n"
    print("脱敏后：")
    print(mask_pii_in_text(demo_md))

    # 跳过 __init__（无需真实凭证）验证 Markdown→block 转换。
    writer = object.__new__(FeishuWriter)
    blocks = FeishuWriter.markdown_to_blocks(writer, demo_md)
    for b in blocks:
        key = next(k for k in b if k not in ("block_type",))
        content = b[key].get("elements", [{}])[0].get("text_run", {}).get("content", "")
        print(f"  block_type={b['block_type']:<2} {key:<9} {content}")
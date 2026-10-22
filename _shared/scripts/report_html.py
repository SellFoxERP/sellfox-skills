#!/usr/bin/env python3
"""报告 HTML 出口：把技能渲染层产出的 Markdown 报告转成自包含 HTML（零网络、零第三方依赖）。

用法（各技能渲染层在写完 .md 后追加一行）：

    from report_html import write_html_report
    html_path = write_html_report(md_text, md_path)   # 写出同名 .html，返回其路径

支持的 Markdown 子集（本仓库渲染层实际使用的全部形态）：
标题 #~###### ｜ 表格 |a|b|（第二行为 --- 分隔行）｜ 无序/有序列表 ｜ **加粗** ｜
`行内代码` ｜ [文本](链接) ｜ 引用块 >（渲染为警示条样式，演示水印落这里）｜
分隔线 --- ｜ 围栏代码 ``` ｜ 段落内换行保留（white-space:pre-line）。
其余字符一律 HTML 转义，不猜测不扩展。
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import List, Optional

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 64px; background: #f6f7f9; color: #1f2328;
       font: 15px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
             "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
main { max-width: 980px; margin: 0 auto; background: #fff; padding: 32px 40px;
       border: 1px solid #e4e7eb; border-radius: 10px; }
h1 { font-size: 22px; margin: 0 0 18px; padding-bottom: 12px; border-bottom: 2px solid #2440b3; }
h2 { font-size: 18px; margin: 26px 0 10px; padding-left: 10px; border-left: 4px solid #2440b3; }
h3 { font-size: 16px; margin: 20px 0 8px; } h4, h5, h6 { font-size: 15px; margin: 16px 0 6px; }
p { margin: 8px 0; white-space: pre-line; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
th, td { border: 1px solid #d9dee4; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #eef1f6; }
tbody tr:nth-child(even) { background: #fafbfc; }
blockquote { margin: 12px 0; padding: 10px 14px; background: #fff7e6;
             border-left: 4px solid #d46b08; color: #7c4a03; border-radius: 4px; }
blockquote p { margin: 0; }
code { background: #f0f2f5; border-radius: 4px; padding: 1px 5px; font-size: 13px;
       font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { background: #f0f2f5; border-radius: 6px; padding: 12px; overflow-x: auto; }
pre code { background: none; padding: 0; }
hr { border: none; border-top: 1px solid #e4e7eb; margin: 20px 0; }
ul, ol { margin: 8px 0; padding-left: 26px; }
footer { max-width: 980px; margin: 10px auto 0; color: #8a919c; font-size: 12px; }
@media print { body { background: #fff; padding: 0; }
  main { border: none; padding: 0; max-width: none; } }
""".strip()


def _mermaid_chart_title(buf: List[str]) -> str:
    for line in buf:
        m = re.match(r'^\s*title\s+"?(.+?)"?\s*$', line)
        if m:
            return m.group(1)
    return "趋势"


def _mermaid_table(buf: List[str]) -> str:
    """xychart-beta → 紧凑数据表（日期 | 值）；解析不出序列则返回空串退回代码块。"""
    xs: List[str] = []
    series: List[tuple] = []
    for line in buf:
        m = re.match(r"^\s*x-axis\s*\[(.+)\]\s*$", line)
        if m:
            xs = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
            continue
        m = re.match(r'^\s*"?(.+?)"?\s*:\s*\[(.+)\]\s*$', line)
        if m and not line.strip().startswith("y-axis"):
            vals = [v.strip() for v in m.group(2).split(",") if v.strip()]
            series.append((m.group(1).strip(), vals))
    if not xs or not series:
        return ""
    title = _mermaid_chart_title(buf)
    head = "<table><thead><tr><th>" + html.escape(title, quote=False) + "</th>" \
           + "".join(f"<th>{html.escape(name, quote=False)}</th>" for name, _ in series) \
           + "</tr></thead><tbody>"
    rows = []
    for idx, x in enumerate(xs):
        cells = "".join(
            f"<td>{html.escape(v, quote=False) if v else '—'}</td>"
            for _, vals in series for v in [vals[idx] if idx < len(vals) else ""]
        )
        rows.append(f"<tr><td>{html.escape(x, quote=False)}</td>{cells}</tr>")
    return head + "".join(rows) + "</tbody></table>"


def _inline(text: str) -> str:
    """行内元素：先整体转义，再套行内码/加粗/链接。"""
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _is_table_sep(line: str) -> bool:
    """表格分隔行：|---|---| 形态（允许每格 : 对齐冒号；内部管道按格拆开判断）。"""
    cells = line.strip().strip("|").split("|")
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells) \
        and any("-" in c for c in cells)


def _table_row(line: str) -> List[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def markdown_to_html(md: str) -> str:
    """Markdown（本仓库子集）→ HTML 片段。"""
    lines = md.splitlines()
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):                       # 围栏代码（mermaid 转简表，其余保持代码块）
            lang = s.strip("`").strip().lower()
            buf: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            if lang == "mermaid":
                table = _mermaid_table(buf)
                if table:
                    out.append(table)
                    continue
            out.append("<pre><code>" + html.escape("\n".join(buf), quote=False) + "</code></pre>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)          # 标题
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^-{3,}$", s):                     # 分隔线
            out.append("<hr>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < n and _is_table_sep(lines[i + 1]):  # 表格
            header = _table_row(s)
            i += 2
            body_rows: List[str] = []
            while i < n and lines[i].strip().startswith("|"):
                body_rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _table_row(lines[i])) + "</tr>")
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header)
                       + "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>")
            continue
        if s.startswith(">"):                          # 引用块（警示条样式）
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip()); i += 1
            out.append("<blockquote><p>" + html.escape(" ".join(b for b in buf if b), quote=False)
                       + "</p></blockquote>")
            continue
        if re.match(r"^[-*]\s+", s):                   # 无序列表
            buf = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                buf.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", lines[i].strip())) + "</li>"); i += 1
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue
        if re.match(r"^\d+[.、]\s+", s):               # 有序列表
            buf = []
            while i < n and re.match(r"^\d+[.、]\s+", lines[i].strip()):
                buf.append("<li>" + _inline(re.sub(r"^\d+[.、]\s+", "", lines[i].strip())) + "</li>"); i += 1
            out.append("<ol>" + "".join(buf) + "</ol>")
            continue
        para: List[str] = []                            # 段落（保留换行）
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|[-*]\s|\d+[.、]\s|>|\||-{3,}$|```)", lines[i].strip()):
            para.append(lines[i].rstrip()); i += 1
        if not para:                                    # 兜底：孤立表格行等未被任何分支消费 → 强制前进，防死循环
            para.append(lines[i].rstrip()); i += 1
        out.append("<p>" + _inline("\n".join(para)) + "</p>")
    return "\n".join(out)


def _title_of(md: str, fallback: str) -> str:
    for line in md.splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            t = re.sub(r"[*`]", "", m.group(1)).strip()
            return html.escape(t, quote=True)
    return html.escape(fallback, quote=True)


def render_html_document(md: str, title: Optional[str] = None) -> str:
    """完整自包含 HTML 文档（内嵌 CSS，无外部资源，可离线打开/打印）。"""
    stem = title or "赛狐经营报告"
    return ("<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            f"<title>{_title_of(md, stem)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
            f"<main>\n{markdown_to_html(md)}\n</main>\n"
            "<footer>由赛狐经营技能包生成 · 数据说明与局限见报告内相应区块 · 演示/样本数据不作为经营结论</footer>\n"
            "</body>\n</html>\n")


def write_html_report(md: str, md_path: Path, title: Optional[str] = None) -> Path:
    """在 .md 同目录写同名 .html（主报告出口），返回 HTML 路径。"""
    html_path = md_path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html_document(md, title), encoding="utf-8")
    return html_path


if __name__ == "__main__":                              # CLI：python3 report_html.py report.md
    import sys
    src = Path(sys.argv[1])
    print(write_html_report(src.read_text(encoding="utf-8"), src))

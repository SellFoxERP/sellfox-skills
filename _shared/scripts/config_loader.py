"""配置加载 + 缺失参数交互补全 + 回写 —— _shared 共享底座。

实现方案第二章规定的**统一交互规范**：

    ① 先读配置文件
    ② 解析不到的参数 → 收集成表，**一次性批量询问**（不逐个打断）
    ③ 用本次回答继续执行
    ④ 询问是否回写配置文件（渐进式记忆，越用越省事）
    ⑤ 输出中标注每个参数的【来源】：配置命中 / 本次填写 / 默认值

核心原则：**默认值必须显式标注，不能混进真实数据里。**

YAML 支持：优先使用 ``PyYAML``（若已安装）；否则回退到内置的**最小 YAML 子集解析器**
（支持嵌套映射、列表、行内 ``{}`` / ``[]``、引号键值、注释），因此本模块**零第三方依赖**。

仅依赖标准库。

示例::

    from config_loader import ConfigLoader, ParamSpec

    cl = ConfigLoader()
    th = cl.load("thresholds")                     # 读 config/thresholds.yaml
    res = cl.resolve_lead_time(msku="MOCK-SKU-001", shipping_method="海运")
    print(res.days, res.source)                    # 45 配置命中

    # 批量补全缺失参数（只问一次）
    # ParamSpec 位置参数：name, file, section（顶层 section，根层键传 ""）, key, label；default 用关键字
    specs = [ParamSpec("default_lead_time", "lead_time", "", "default", "头程默认到货天数", default=45)]
    values = cl.ensure_params(specs, answers={"default_lead_time": 50})
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # 可选依赖：装了 PyYAML 就用它，没装也不影响运行。
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover - 环境无 PyYAML
    _pyyaml = None

#: 默认兜底 shared_root（本文件位于 _shared/scripts/ 下，向上两级即 _shared）。
_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_SHARED_ROOT = _THIS_DIR.parent

#: 参数来源标注（写进报告，让使用者一眼看出置信度）。
SRC_CONFIG = "配置命中"
SRC_ANSWERED = "本次填写"
SRC_DEFAULT = "默认值"


class ConfigError(RuntimeError):
    """配置加载/回写失败。"""


# ===========================================================================
# 最小 YAML 解析器（PyYAML 不可用时的回退）
# ===========================================================================


def _strip_comment(line: str) -> str:
    """去掉行尾注释（不碰引号内内容）。"""
    out: List[str] = []
    quote: Optional[str] = None
    esc = False
    for ch in line:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            out.append(ch)
            quote = ch
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_kv(text: str) -> Tuple[str, str]:
    """按第一个不在引号内的 ``:`` 拆成键值。"""
    quote: Optional[str] = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":":
            key = text[:i].strip().strip('"').strip("'")
            return key, text[i + 1 :].strip()
    return text.strip().strip('"').strip("'"), ""


def _split_flow(text: str) -> List[str]:
    """按逗号拆分行内流式集合（忽略引号/括号内的逗号）。"""
    parts: List[str] = []
    depth = 0
    quote: Optional[str] = None
    cur: List[str] = []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_scalar(text: str) -> Any:
    """把标量字符串解析成 int/float/bool/None/str/流式集合。"""
    s = text.strip()
    if s == "":
        return None
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        result: Dict[str, Any] = {}
        for part in _split_flow(inner):
            k, v = _split_kv(part)
            result[k] = _parse_scalar(v)
        return result
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _mini_yaml_load(text: str) -> Any:
    """解析最小 YAML 子集（缩进映射 / 列表 / 行内集合 / 引号 / 注释）。"""
    rows: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        cleaned = _strip_comment(raw)
        if cleaned.strip() == "":
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        rows.append((indent, cleaned.strip()))
    if not rows:
        return {}

    pos = 0

    def parse_block(indent: int) -> Any:
        nonlocal pos
        node: Any = None
        while pos < len(rows):
            cur_indent, content = rows[pos]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                # 非法缩进，跳过以防死循环
                pos += 1
                continue
            if content.startswith("- "):
                if node is None:
                    node = []
                if not isinstance(node, list):
                    break
                pos += 1
                rest = content[2:].strip()
                if rest == "":
                    node.append(parse_block(indent + 2) if pos < len(rows) and rows[pos][0] > indent else None)
                elif ":" in rest and not rest.startswith(("'", '"')):
                    item: Dict[str, Any] = {}
                    k, v = _split_kv(rest)
                    item[k] = _parse_scalar(v) if v != "" else (
                        parse_block(indent + 4) if pos < len(rows) and rows[pos][0] > indent + 2 else None
                    )
                    # 同一列表项的后续键
                    while (
                        pos < len(rows)
                        and rows[pos][0] == indent + 2
                        and not rows[pos][1].startswith("- ")
                    ):
                        k2, v2 = _split_kv(rows[pos][1])
                        pos += 1
                        item[k2] = _parse_scalar(v2) if v2 != "" else None
                    node.append(item)
                else:
                    node.append(_parse_scalar(rest))
            else:
                if node is None:
                    node = {}
                if not isinstance(node, dict):
                    break
                pos += 1
                key, value = _split_kv(content)
                if value == "":
                    if pos < len(rows) and rows[pos][0] > indent:
                        node[key] = parse_block(rows[pos][0])
                    else:
                        node[key] = None
                else:
                    node[key] = _parse_scalar(value)
            if node is None:
                break
        return node

    return parse_block(rows[0][0])


def load_yaml_file(path: Path) -> Dict[str, Any]:
    """读取 YAML 文件为字典（优先 PyYAML，回退内置解析器）。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if _pyyaml is not None:
        try:
            data = _pyyaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # 解析失败则回退
            raise ConfigError(f"PyYAML 解析 {path} 失败：{exc}") from exc
    data = _mini_yaml_load(text)
    return data if isinstance(data, dict) else {}


# ===========================================================================
# 回写（保留注释，按 section 插入/更新键）
# ===========================================================================


def _dump_scalar(value: Any) -> str:
    """把标量渲染成 YAML 片段（必要时加引号）。"""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s == "" or re.search(r"[:#\[\]{},]|^\s|\s$", s):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _dump_key(key: str) -> str:
    """渲染映射键（含特殊字符时加引号，如 "11-01~12-25"）。"""
    if re.search(r"^[A-Za-z_][\w\-]*$", str(key)):
        return str(key)
    return '"' + str(key).replace('"', '\\"') + '"'


def write_back_section(path: Path, section: str, entries: Dict[str, Any]) -> Path:
    """在指定 top-level section 下写入/更新若干键，**保留文件其余注释与结构**。

    会在写入前生成 ``<name>.bak`` 备份。返回实际写入路径。
    """
    if not entries:
        return path
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    sec_start: Optional[int] = None
    sec_indent = 0
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if line.strip() == f"{section}:":
            sec_start = idx
            sec_indent = len(line) - len(line.lstrip(" "))
            break

    if sec_start is None:
        # section 为空字符串表示根层键：直接写根缩进，不能输出孤立的 ":" 行（会破坏 YAML）
        if section:
            lines.append(f"{section}:")
        child = "  " if section else ""
        for k, v in entries.items():
            lines.append(f"{child}{_dump_key(k)}: {_dump_scalar(v)}")
    else:
        end = len(lines)
        for idx in range(sec_start + 1, len(lines)):
            s = lines[idx]
            if s.strip() == "" or s.lstrip().startswith("#"):
                continue
            if (len(s) - len(s.lstrip(" "))) <= sec_indent:
                end = idx
                break
        child_indent = " " * (sec_indent + 2)
        remaining = dict(entries)
        body = lines[sec_start + 1 : end]
        new_body: List[str] = []
        for line in body:
            s = line.strip()
            if not s.startswith("#") and ":" in s:
                key = _split_kv(s)[0]
                if key in remaining:
                    new_body.append(f"{child_indent}{_dump_key(key)}: {_dump_scalar(remaining.pop(key))}")
                    continue
            new_body.append(line)
        for k, v in remaining.items():
            new_body.append(f"{child_indent}{_dump_key(k)}: {_dump_scalar(v)}")
        lines[sec_start + 1 : end] = new_body

    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ===========================================================================
# 参数规格 / 到货周期结果
# ===========================================================================


@dataclass
class ParamSpec:
    """一个待补全参数的规格说明。

    :param name: 逻辑名（供 :meth:`ConfigLoader.ensure_params` 返回）。
    :param file: 配置文件名（不含 ``.yaml``），如 ``lead_time``。
    :param section: 顶层 section 名；空字符串表示根层键。
    :param key: 键名。
    :param label: 展示给用户的中文说明。
    :param default: 兜底默认值。
    """

    name: str
    file: str
    section: str
    key: str
    label: str
    default: Any = None


@dataclass
class LeadTimeResult:
    """到货周期解析结果。

    :param days: 最终天数（含旺季系数）。
    :param source: 来源标注（配置命中 / 本次填写 / 默认值）。
    :param layer: 命中的层级（by_sku / by_supplier / by_shipping_method / default）。
    :param base_days: 未乘旺季系数的基础天数。
    :param seasonal_factor: 命中的旺季系数（无则 1.0）。
    """

    days: float
    source: str
    layer: str
    base_days: float
    seasonal_factor: float = 1.0

    def __str__(self) -> str:  # pragma: no cover
        extra = f" × 旺季{self.seasonal_factor}" if self.seasonal_factor != 1.0 else ""
        return f"{self.days:.1f}天（{self.layer}·{self.source}{extra}）"


# ===========================================================================
# 加载器
# ===========================================================================


class ConfigLoader:
    """配置加载器（含缓存、路径解析、到货周期解析、批量补全与回写）。

    :param shared_root: ``_shared/`` 根目录。解析优先级：
        显式参数 > 环境变量 ``SELLFOX_SHARED_ROOT`` > 技能侧 ``config/paths.yaml``
        的 ``shared_root`` > 本文件相对路径。
    """

    def __init__(self, shared_root: Optional[str] = None) -> None:
        self.shared_root = self._resolve_root(shared_root)
        self.config_dir = self.shared_root / "config"
        self._cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _resolve_root(explicit: Optional[str]) -> Path:
        """按优先级解析 shared_root（不写死相对 ``../_shared/``）。"""
        if explicit:
            return Path(explicit).expanduser().resolve()
        env = os.environ.get("SELLFOX_SHARED_ROOT")
        if env:
            return Path(env).expanduser().resolve()
        # 技能侧 config/paths.yaml 可覆盖
        for candidate in (Path.cwd() / "config" / "paths.yaml", Path.cwd() / "paths.yaml"):
            if candidate.exists():
                data = load_yaml_file(candidate)
                if data.get("shared_root"):
                    return Path(str(data["shared_root"])).expanduser().resolve()
        return _DEFAULT_SHARED_ROOT

    # -- 读 ---------------------------------------------------------------

    def config_path(self, name: str) -> Path:
        """返回配置文件绝对路径（补 ``.yaml``）。"""
        filename = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
        return self.config_dir / filename

    def load(self, name: str, *, reload: bool = False) -> Dict[str, Any]:
        """加载配置（带缓存）。文件不存在返回空字典（不抛异常）。"""
        if not reload and name in self._cache:
            return self._cache[name]
        data = load_yaml_file(self.config_path(name))
        if name in ('reports', 'reports.yaml', 'reports.yml'):
            from merchant_setup import overlay_reports
            data = overlay_reports(data)
        self._cache[name] = data
        return data

    def section(self, name: str, section: str) -> Dict[str, Any]:
        """取某配置文件的顶层 section。"""
        value = self.load(name).get(section)
        return value if isinstance(value, dict) else {}

    # -- 到货周期解析 -----------------------------------------------------

    def resolve_lead_time(
        self,
        msku: Optional[str] = None,
        supplier: Optional[str] = None,
        shipping_method: Optional[str] = None,
        *,
        on_date: Optional[_date] = None,
        lead_time_override: Optional[Dict[str, Any]] = None,
    ) -> LeadTimeResult:
        """按 ``by_sku > by_supplier > by_shipping_method > default`` 解析到货周期。

        :param on_date: 用于匹配旺季系数的日期（默认今天）。
        :param lead_time_override: 本次交互填写的值，形如 ``{"by_sku": {"A001": 20}}``，
            其优先级高于配置文件（来源标为「本次填写」）。
        """
        cfg = dict(self.load("lead_time"))
        override = lead_time_override or {}
        factor, _range = self._seasonal_factor(cfg, on_date or _date.today())

        for layer, key in (("by_sku", msku), ("by_supplier", supplier), ("by_shipping_method", shipping_method)):
            if key is None:
                continue
            for source_cfg, src in ((override, SRC_ANSWERED), (cfg, SRC_CONFIG)):
                table = source_cfg.get(layer) or {}
                if key in table:
                    base = float(table[key])
                    return LeadTimeResult(base * factor, src, layer, base, factor)

        default = cfg.get("default")
        if default is None:
            default = 45
            src = SRC_DEFAULT
        else:
            src = SRC_CONFIG if "default" in cfg else SRC_DEFAULT
        base = float(default)
        return LeadTimeResult(base * factor, src, "default", base, factor)

    @staticmethod
    def _seasonal_factor(cfg: Dict[str, Any], on_date: _date) -> Tuple[float, Optional[str]]:
        """匹配旺季系数（支持 ``"MM-dd~MM-dd"`` 跨年区间，多个区间相乘）。"""
        table = cfg.get("seasonal_adjustment") or {}
        factor = 1.0
        hit: Optional[str] = None
        for span, mult in table.items():
            if "~" not in str(span):
                continue
            start_s, end_s = str(span).split("~", 1)
            try:
                start = (_date(on_date.year, *_parse_md(start_s)))
                end = _date(on_date.year, *_parse_md(end_s))
            except ValueError:
                continue
            if end < start:  # 跨年区间
                in_range = on_date >= start or on_date <= end
            else:
                in_range = start <= on_date <= end
            if in_range:
                try:
                    factor *= float(mult)
                    hit = str(span)
                except (TypeError, ValueError):
                    pass
        return factor, hit

    # -- 批量补全 + 回写 --------------------------------------------------

    def ensure_params(
        self,
        specs: Sequence[ParamSpec],
        *,
        answers: Optional[Dict[str, Any]] = None,
        allow_write_back: bool = True,
        write_back_confirmed: Optional[bool] = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> Dict[str, Tuple[Any, str]]:
        """批量补全缺失参数，返回 ``{name: (value, source)}``。

        流程：读配置 → 收集缺失项（**一次性成表询问**）→ 询问是否回写 → 输出标注来源。

        :param answers: 预置回答（供非交互/测试环境使用，命中即视为「本次填写」）。
        :param write_back_confirmed: ``True``/``False`` 直接决定是否回写；``None`` 则询问。
        """
        answers = dict(answers or {})
        resolved: Dict[str, Tuple[Any, str]] = {}
        missing: List[ParamSpec] = []

        for spec in specs:
            cfg = self.load(spec.file)
            container = cfg if not spec.section else (cfg.get(spec.section) or {})
            value = container.get(spec.key) if isinstance(container, dict) else None
            if value not in (None, "", [], {}):
                resolved[spec.name] = (value, SRC_CONFIG)
            else:
                missing.append(spec)

        if not missing:
            return resolved

        # ② 一次性列成一张表
        output_fn("")
        output_fn("以下参数解析不到，请一次性填写（回车跳过则用默认值）：")
        output_fn("┌────┬──────────────────────────────┬────────────────┐")
        output_fn("│ 序号 │ 参数                          │ 默认值          │")
        output_fn("├────┼──────────────────────────────┼────────────────┤")
        for i, spec in enumerate(missing, 1):
            output_fn(f"│ {i:>2} │ {spec.label:<28} │ {str(spec.default):<14} │")
        output_fn("└────┴──────────────────────────────┴────────────────┘")

        answered_entries: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for spec in missing:
            if spec.name in answers:
                raw: Any = answers[spec.name]
                source = SRC_ANSWERED
            else:
                try:
                    raw = input_fn(f"  {spec.label}（{spec.key}，默认 {spec.default}）: ").strip()
                except EOFError:
                    raw = ""
                if raw == "":
                    raw = spec.default
                    source = SRC_DEFAULT
                else:
                    source = SRC_ANSWERED

            value = _coerce(raw, spec.default)
            resolved[spec.name] = (value, source)
            if source == SRC_ANSWERED:
                answered_entries.setdefault(spec.file, {}).setdefault(spec.section, {})[spec.key] = value

        # ④ 询问是否回写（渐进式记忆）
        if allow_write_back and answered_entries:
            if write_back_confirmed is None:
                try:
                    answer = input_fn("是否把本次填写回写到配置文件，供下次复用？(y/N): ").strip().lower()
                except EOFError:
                    answer = "n"
                write_back_confirmed = answer in ("y", "yes", "是")
            if write_back_confirmed:
                for filename, sections in answered_entries.items():
                    for section_name, entries in sections.items():
                        write_back_section(self.config_path(filename), section_name, entries)
                output_fn("✅ 已回写配置（下次将自动命中，不再询问）。")
                for filename in answered_entries:  # 涉及几个文件就失效几个缓存
                    self._cache.pop(filename, None)

        return resolved

    # -- 输出标注 ---------------------------------------------------------

    @staticmethod
    def describe_sources(resolved: Dict[str, Tuple[Any, str]]) -> str:
        """把 ``{name: (value, source)}`` 渲染为可粘贴进报告的来源标注表。"""
        lines = ["| 参数 | 取值 | 来源 |", "|---|---|---|"]
        for name, (value, source) in resolved.items():
            flag = " ⚠️" if source == SRC_DEFAULT else ""
            lines.append(f"| {name} | {value} | {source}{flag} |")
        return "\n".join(lines)


def _parse_md(text: str) -> Tuple[int, int]:
    """解析 ``"11-01"`` → ``(11, 1)``。"""
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", text.strip())
    if not m:
        raise ValueError(text)
    return int(m.group(1)), int(m.group(2))


def _coerce(raw: Any, default: Any) -> Any:
    """按 default 的类型转换用户输入。"""
    if not isinstance(raw, str):
        return raw
    if isinstance(default, bool):
        return raw.lower() in ("y", "yes", "true", "1", "是")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(float(raw))
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


if __name__ == "__main__":  # pragma: no cover
    cl = ConfigLoader()
    print(f"shared_root = {cl.shared_root}")
    print(f"thresholds.sales = {cl.section('thresholds', 'sales')}")
    print(f"lead_time default = {cl.load('lead_time').get('default')}")
    res = cl.resolve_lead_time(shipping_method="空运")
    print(f"空运到货周期 -> {res}")
    print(cl.describe_sources({"示例参数": (30, SRC_CONFIG)}))

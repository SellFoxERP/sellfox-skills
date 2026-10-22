# _shared/ —— 专项技能（01~09、13）的共享底座

> **这是整个方案能不能用得长久的关键。** 没有它，各专项技能会各自重复实现调用逻辑，字段口径会逐渐分叉。
>
> ⚠️ **它不是技能**（没有 SKILL.md、没有触发机制）。专项技能 01~09 与 13 通过各自 `references/` 里的说明 +
> 脚本内的路径解析来引用它；通用技能 10 与主题技能 11/12 自带独立脚本，不直接依赖 `_shared`。

---

## 批量安装与路径

源码目录保留同级 `_shared/` 即可运行。商家批量安装时，安装器将底座复制为技能目录下的
`_sellfox_shared/` 并写入消费底座各技能（01~09、13）的 `config/paths.yaml`。店铺、报表、阈值只维护一份。
优先级：显式参数（支持时）> `SELLFOX_SHARED_ROOT` > paths.yaml > 自动定位。
计算脚本不连接赛狐；飞书输出和审批通知是可选联网功能。

## 目录结构

```
_shared/
├── README.md                    本文件
├── SELLFOX_CLIENT.md            ⭐ v2 agent 调用契约（12 条纪律 + 取数计划模板）
├── APPROVAL.md                  人机审核统一规范（L1~L4 / 三段式 / 飞书审批）
├── METRIC_FORMULAS.md           指标口径白皮书（含 profit_caliber_type 选择）
├── FIELD_DICTIONARY.md          字段字典（字段 → 含义 → 所属工具）
├── config/                      ⭐ 全部小写文件名，唯一路径
│   ├── thresholds.yaml          预警阈值（含 approval 审批闸门）
│   ├── lead_time.yaml           头程到货周期（三级配置 + 旺季系数）
│   ├── shops.yaml               店铺与负责人
│   ├── targets.yaml             经营目标
│   └── reports.yaml             ⭐ report_id（自动发现后可缓存）
├── scripts/
│   ├── sellfox_offline.py       v3 离线工具（信封解析/演示指纹/空数据判定）——脚本用这个
│   ├── validate_raw.py          落盘校验器 CLI（校验 out/raw/*.json）
│   ├── feishu_writer.py         飞书写入封装（token 缓存 + markdown→block + PII 脱敏）
│   ├── approval.py              审批流封装（提案 / 飞书推送 / 确认解析 / 变更日志 / 累计闸）
│   └── config_loader.py         配置加载 + 批量补全 + 回写 + 来源标注
```

> **命名唯一化**：文档目录一律 `references/`（复数）；配置目录一律 `_shared/config/`（小写）；
> 配置文件名一律全小写。
> **v3 定位变化**：`_shared` 服务**计算面**（配置/离线工具/飞书/审批）；
> 取数面（MCP 调用）由 agent 按各技能取数计划在客户端完成，不经 `_shared` 脚本。

---

## 脚本职责速查

| 脚本 | 关键 API | 依赖 |
|---|---|---|
| `sellfox_offline.py` | `load_envelope` / `RawEnvelope` / `detect_demo_environment` / `is_no_data` / `detect_date_filter_unreliable` | 仅标准库 |
| `validate_raw.py` | CLI：`--dir out/raw --step 名:列表字段 --window`（退出码 0=就绪 / 2=缺失） | 仅标准库 |
| `feishu_writer.py` | `FeishuWriter.create_doc` / `write_blocks` / `markdown_to_blocks` / `mask_pii` / `guard_demo` | 仅标准库 |
| `approval.py` | `Proposal` / `ChangeItem` / `AccumulatorGate` / `push_proposal` / `parse_confirmation` / `record_changes` / `verify_read_back` | 仅标准库 |
| `config_loader.py` | `ConfigLoader.load` / `resolve_lead_time` / `ensure_params` / `describe_sources` | 仅标准库（PyYAML 可选） |

### 最小示例（v3：计算面读落盘信封）

```python
import sys
sys.path.insert(0, "<shared_root>/scripts")

from config_loader import ConfigLoader
from sellfox_offline import load_envelope, detect_demo_environment

cfg = ConfigLoader()                                   # 自动定位 config/
thresholds = cfg.section("thresholds", "sales")        # {'drop_pct_vs_7d': 30, ...}

env = load_envelope("<skill>/out/raw/profit_shop.json")  # agent 落盘的信封
rows = env.rows("reportList")                            # 列表字段从契约 pagination 来
print(env.summary())                                     # "profit_shop.json: status=complete | rows=20 | ..."
if detect_demo_environment(env.obj):
    print("⚠️ 演示数据，输出需加水印并默认阻断写入")
```

---

## 统一交互规范（所有技能遵守）

```
① 先读配置文件
② 解析不到的参数 → 收集成表，一次性批量询问（不逐个打断）
③ 用本次回答继续执行
④ 询问是否回写配置文件（渐进式记忆，越用越省事）
⑤ 输出中标注每个参数的【来源】：配置命中 / 本次填写 / 默认值
```

> **核心原则**：**默认值必须显式标注，不能混进真实数据里。**

---

## 必读顺序（开发技能时）

1. `SELLFOX_CLIENT.md` —— 调用契约，**先读这个**
2. `METRIC_FORMULAS.md` —— 指标口径，避免与财务对不上账
3. `FIELD_DICTIONARY.md` —— 字段名核对，**不得凭记忆书写**
4. `APPROVAL.md` —— 若技能涉及写操作，**必须遵守**

---

## 前置条件（部署检查清单）

- [ ] 拿到赛狐 MCP Key（沙箱 Key 可验证连通性；业务决策请使用生产数据源配置）
- [ ] 自定义报表可由助手自动发现并核对用途（技能 01/02/05 需要；无可用报表时走简版降级，不要求手填 report_id）
- [ ] 飞书自建应用（记录 `app_id` / `app_secret`），**并把应用加为文档协作者**
- [ ] 与业务方确认 `config/thresholds.yaml` 的预警阈值
- [ ] 确认利润口径（`settlement` / `shipment`）
- [ ] 填 `config/shops.yaml`（告警路由）与 `config/targets.yaml`（达成率）
- [ ] 明确广告变更审批人（技能 6）
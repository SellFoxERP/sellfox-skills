# 人机审核统一规范（APPROVAL.md）

> **读写技能的基础。所有写操作走同一条通道。**
> 依据《12-Skill方案-最终版.md》第三章。配套实现：`scripts/approval.py`。
>
> **为什么需要它**：亚马逊官方 Skill 工具包 README 原文 ——
> *"Write actions are always human-in-the-loop (drafted for your approval)."*
> 本方案与官方原则一致：**写操作一律先出提案，人工批准后才执行**。

---

## 目录

- [一、写操作风险分级（L1~L4）](#一写操作风险分级l1l4)
- [二、三段式流程（不可跳过）](#二三段式流程不可跳过)
- [三、强制约束（六条）](#三强制约束六条)
- [四、四个必须补上的安全机制](#四四个必须补上的安全机制)
- [五、累计闸](#五累计闸)
- [六、飞书审批流](#六飞书审批流)
- [七、代码用法](#七代码用法)
- [八、写进每个读写技能 SKILL.md 的硬约束](#八写进每个读写技能-skillmd-的硬约束)

---

## 一、写操作风险分级（L1~L4）

| 级别 | 操作 | 默认行为 |
|---|---|---|
| **L1 低风险** | `create_ad_download_task`（下载报表，**只读性质的写**） | 直接执行 |
| **L2 中风险** | `edit_sp_campaign` / `edit_sb_campaign`（改预算/状态）、`edit_sp_targeting` / `edit_sb_targeting`（改竞价）、`create_sp_*_targeting`（加投放）、**`add_custom_report_index` / `update_custom_report_index`（自定义指标）** | **dry-run 预览 → 逐项确认 → 执行**，单次 ≤20 条，幅度 ≤±30% |
| **L3 高风险** | 批量暂停全部活动、预算清零、删除投放 | **默认禁止**，需显式开启 + 二次确认 |
| **L4 极高风险** | 改 Listing、改价格 | **技能不做**（MCP 也无此能力） |

> ⚠️ **勘误**：早期版本把 `add/update_custom_report_index` 定为 L1。
> 改指标定义会影响**全公司报表口径**，且 MCP 的 `isError` 信号不可靠（参数错误时返回 `false`），
> **静默写坏一个指标公式可能几周后才被发现**。故上调为 **L2**，必须 dry-run 对比新旧公式 +
> 逐项确认 + **写前快照旧公式**。

代码常量：`approval.RISK_LEVELS`。

### 可用写工具与接口上限（实测确认）

| 场景 | 工具 | 接口上限 | **我们的限制** |
|---|---|---|---|
| 调预算 / 暂停活动 | `edit_sp_campaign`、`edit_sb_campaign` | 100 条/次 | **20** |
| 调竞价 | `edit_sp_targeting`、`edit_sb_targeting` | 100 条/次 | **20** |
| 加否定关键词 | `create_sp_negative_keyword_targeting` | 100 条/次 | 20 |
| 加否定商品 | `create_sp_negative_product_targeting` | 100 条/次 | 20 |
| 关否定投放 | `close_sp_negative_targeting` | 100 条/次 | 20 |
| 新增关键词投放 | `create_sp_keyword_targeting` | 100 条/次 | 20 |
| 调广告组 | `edit_sp_ad_group`、`edit_sb_ad_group` | 100 条/次 | 20 |
| 自定义指标 | `add_custom_report_index`、`update_custom_report_index` | — | 逐项确认 |

> `edit_sp_campaign` 的 `state` 取值**仅用实测确认的 `enabled` / `paused` / `archived`**。

---

## 二、三段式流程（不可跳过）

```
① 诊断（只读）
     ↓
② 提案（dry-run，输出「变更前后对比表」）
     ↓
③ 人工确认 ← 飞书卡片/文档，@ 负责人
     ↓
④ 执行（写）
     ↓
⑤ 记录 + 可回滚
```

**每一步都不可跳过。** 尤其：

- ② 必须是 **dry-run** —— 只输出对比表，**不碰系统**。
- ④ 只执行**被批准的项**（`Proposal.approved_subset(...)`）。
- ⑤ 写 `ad_changes.jsonl`，保证留痕与可回滚。

---

## 三、强制约束（六条）

1. **默认 dry-run**：任何写操作先出「变更前后对比表」。
2. **单次变更 ≤ 20 条**（远低于接口的 100 上限）。
3. **预算调整幅度 ≤ ±30%**。
4. **提案必须含回滚方案**（写前快照旧值，即 `before` 字段）。
5. **每次执行记录变更日志**到 `ad_changes.jsonl`：
   时间 / 对象 / 变更前 / 变更后 / 操作人。
6. **禁止清单**：批量全暂停、预算清零、删除投放。

`sellfox_client` / `approval.py` 的 `Proposal.validate()` 会强校验 1~3、6。

> 💡 **为什么单次限 20 而不是 100**：接口允许 100，但一次改 100 个活动，人工根本审不过来。
> 限额的本质是**让「人工确认」这件事真的有效**，而不是形式。

### 指标定义的额外约束（L2 中的特例）

- **必须 dry-run 对比新旧公式**（`update_custom_report_index` 传 `index_formula`）。
- **写前快照旧公式**（`get_custom_report_index_page_list` 取回当前定义）。
- **逐项确认**，不可批量静默执行。

---

## 四、四个必须补上的安全机制

### ① 写后 read-back 校验（最关键）

MCP 的 `isError` 不可靠 —— 参数错误时返回 `false`，意味着**写操作可能「静默失败」却没被发现**。

```
写操作执行后，必须同 session 回读目标对象，比对目标值：
  IF 回读值 == 期望值  → 记 success
  ELSE                → 记 FAILED，触发失败路径（告警 + 不更新本地状态）
```

> 没有 read-back，技能会报告「已修改 12 个活动」，而实际一个都没改。

API：`approval.verify_read_back(client, read_tool, read_args, id_field=..., id_value=..., field_name=..., expected=...)`
→ 返回 `(ok, actual)`，并用宽松比较容忍 `"100"`/`100` 差异。

> ⚠️ 广告活动列表用游标分页（`itemList`），回读时注意 `list_key` 与分页。

### ② 累计闸（防连续调用绕过单次限额）

「单次 ≤20 条」挡不住连续调用 10 次。见 [§五](#五累计闸)。

### ③ PII 脱敏（对外写入前强制）

订单含 `buyerEmail` / `buyerName`（及买家地址）。任何**写入飞书或外部文档**的内容，
必须过白名单：

```
写入前 → 剔除或掩码 buyerEmail / buyerName / 买家地址
       → 在文档中标注「已脱敏」
飞书文档默认不公开分享
```

API：`feishu_writer.mask_pii(obj)`、`mask_pii_in_text(text)`（`markdown_to_blocks` 内自动调用）。

### ④ 演示环境运行期识别

数据源可能是演示/沙箱环境（以客户端配置的端点/Key 为准）。技能必须**在运行时识别**，而不是靠人记得：

```
检测指纹：MOCK- 前缀 / demo.example.com / 店铺名含「演示」
命中 → 输出顶部加水印「⚠️ 演示数据，非真实经营数据」
     → 默认阻断写飞书（可显式覆盖）
```

API：`FeishuWriter.guard_demo(payload)`（默认抛错阻断；`override=True` 放行但仍加水印）。

> 数据源可用性（客户端 MCP 就绪、取数落盘就绪）应做成技能可运行的**前置校验**，而不是文档里的一句提醒。

---

## 五、累计闸

| 闸门 | 限制 | 配置键（`thresholds.yaml → approval`） |
|---|---|---|
| 单 campaign 24h 内调整次数 | ≤ 2 次 | `max_changes_per_campaign_24h` |
| 单 campaign 24h 净变化 | ≤ ±30%（**防止 ±30% 复利累加**） | `max_net_change_pct_24h` |
| 全局单日预算增量总额 | 需配置上限 | `max_daily_budget_increment` |
| 执行前查 `ad_changes.jsonl` 去重 | 同一对象同一天不重复改 | — |

**为什么需要**：单次合规、连续调用就能越界。例如 100 元预算，一次 +30% 到 130，
再一次 +30% 到 169 —— 两次单次都合规，累计却 +69%。

API：

```python
gate = AccumulatorGate("ad_changes.jsonl", limits=cfg["approval"])
violations = gate.check(proposal.items)   # 返回违规说明列表；空 = 通过
if violations:
    print("\n".join(violations))           # 拒绝执行
```

> 累计闸读取的是**变更日志**（`ad_changes.jsonl`），所以日志必须真实、及时写入；
> 只记成功（`status="success"`）的记录参与累计。

---

## 六、飞书审批流

```
AI 助手 生成提案
   ↓
Feishu：创建审批文档 + 发送卡片消息 @ 负责人
   ↓
人工在飞书确认（✅/❌，或回复「批准 1,3,5」）
   ↓
技能读取确认结果 → 只执行被批准的项
   ↓
Feishu：回写执行结果 + 写 ad_changes.jsonl
```

> 💡 **为什么用飞书做审批**：广告变更需要**留痕**和**责任人**。飞书文档天然具备这两点，
> 比在终端里敲 y/n 可靠得多。

### 确认文本解析规则

`approval.parse_confirmation(text, total=N)` → `(decision, approved_indexes)`：

| 输入 | decision | approved |
|---|---|---|
| `批准 1,3,5` | `approve` | `[1,3,5]` |
| `1,3,5`（只回序号） | `approve` | `[1,3,5]` |
| `批准全部` / `全部批准` / `✅` / `同意` | `approve_all` | `[1..N]` |
| `驳回` / `❌` / `不批准` | `reject` | `[]` |
| 其它 | `unclear` | `[]` |

> `unclear` 必须**回到人工再问**，**不得默认执行**。

### 飞书实现要点

- 需**自建应用**（`app_id` / `app_secret`），`tenant_access_token` 鉴权（缓存 2h）。
- **写入前必须把应用添加为文档协作者**，否则返回 403。
- 创建文档接口限流 **3 次/秒**（错误码 `99991400`）；同文件夹创建必须串行。
- 创建文档后立即写入可能遇 block 索引问题 → **先建文档 → 追加 block → 校验**。
- 卡片消息可走**群机器人 Webhook**（`FEISHU_APPROVAL_WEBHOOK`）或应用消息 API。

---

## 七、代码用法

```python
from approval import (
    Proposal, ChangeItem, AccumulatorGate,
    push_proposal, parse_confirmation, record_changes, verify_read_back,
)

# ② 生成 dry-run 提案
p = Proposal(skill="ad-optimizer", title="广告优化提案 2026-09-29", risk_level="L2")
p.add(ChangeItem(
    tool="edit_sp_campaign", target_type="campaign", target_id="C001",
    field="budget", before=100, after=120, reason="ACOS 低于目标、曝光不足",
))
p.add(ChangeItem(
    tool="create_sp_negative_keyword_targeting", target_type="keyword",
    target_id="KW002", field="add", before=None, after="free shipping",
    reason="7 天高花费零转化",
))
p.validate(limits=cfg["approval"])            # 违反强制约束会抛 ApprovalError

# 累计闸
gate = AccumulatorGate("ad_changes.jsonl", limits=cfg["approval"])
if gate.check(p.items):
    raise SystemExit("累计闸拦截")

# ③ 推送飞书审批
push_proposal(p, writer=feishu_writer, owner_open_id="ou_xxx")

# 人工回复后
decision, approved = parse_confirmation("批准 1", total=len(p.items))
if decision in ("approve", "approve_all"):
    items = p.approved_subset(approved) if decision == "approve" else p.items

    # ④ 执行 + ⑤ read-back 校验 + 记录
    for it in items:
        # ... 调用 it.tool，参数含 it.target_id / it.field / it.after ...
        ok, actual = verify_read_back(
            client, "get_sp_campaign_list", {"shop_id": "10001"},
            id_field="campaignId", id_value=it.target_id,
            field_name="budget", expected=it.after,
        )
        record_changes([it], operator="ou_xxx", status="success" if ok else "failed")
```

---

## 八、写进每个读写技能 SKILL.md 的硬约束

读写技能（技能 6 广告优化、技能 7 指标固化）的 SKILL.md **必须**写明：

1. 默认 dry-run，任何写操作先出「变更前后对比表」；
2. 单次变更 ≤20 条；
3. 预算调整幅度 ≤±30%；
4. 提案必须含回滚方案（写前快照旧值）；
5. 每次执行记录变更日志；
6. 禁止清单：批量全暂停、预算清零、删除投放；
7. **写后必须 read-back 校验**（不能只信 `isError`）；
8. 执行前过**累计闸**；
9. 写飞书前过 **PII 脱敏**；
10. 演示环境默认**阻断写入**。

> **顺序原则**：先做只读、高价值、低风险的建立信任；再做低风险写熟悉审核流；
> **最后做高风险的广告**。反过来做，一次误操作就会失去团队信任。
> 技能 6 建议先跑 **2~4 周纯 dry-run**，只出提案不执行，团队认可后再开启执行，
> 且从 L1 级变更（加否定词）开始。
# 公式处理与字段核对（formula-and-fields.md）

> **写 `index_formula` 之前必读。** 本文件规定本技能对公式的**诚实边界**与字段核对方法。

## 一、为什么不能臆造 `index_formula`

- 137 个工具文档中，`index_formula` 只写了一句「指标公式（必填）」，
  **没有任何语法说明**（运算符、字段记号、引用方式、括号、函数）。
- 因此**任何自造公式都可能写坏全公司报表口径**，且因为 MCP 的 `isError` 在参数错误时
  返回 `false`（铁律 4），**静默失败可能几周后才被发现**。
- **规则**：`formula_status: needs_human` 的指标**一律不进入提案**。
  只有人去赛狐网页端确认过语法、并把确认语法回填到 `config/metric_governance.yaml` 后，
  该指标才升级为 `verified` 并可写入。

## 二、`type` 取值（实测自工具文档）

| type | 含义 |
|---|---|
| `0` | 其他 |
| `1` | 数量 |
| `2` | 货币 |
| `3` | 百分比 |

## 三、字段核对方法（写 `source_fields` 前必做）

每个 `source_fields` 必须**在权威文档中核对存在**，不得凭记忆：

- `_shared/FIELD_DICTIONARY.md` — 字段字典（字段名以此为准，不凭记忆）。
- `_shared/METRIC_FORMULAS.md` — 口径白皮书（指标→字段→工具）。
- 工具参数以网关当次 `sf_describe` 返回的契约为准。

核对要点：

1. **字段属于哪个工具**。两个报表字段不通用（实测确认）：
   `reimbursementAmount` / `warehouseLostAmount` / `missingFromInboundAmount` /
   `deferredAmount` / `purchaseCost` / `headTripPrice` **只在自定义报表宽表（489 字段）**，
   **不在 `get_profit_report_by_msku`**。（宽表查询/字段核对转 custom-report-analysis 技能；
   本技能只治理指标定义，不查询宽表数据。）
2. **列表字段名以当次契约为准**：网关分页默认标准化为 `rows`（raw 模式才出现 `reportList`/`itemList`）；读取用共享库自动识别。
3. **币种**：`get_online_product_page_list.currency` 是 **USD**，
   其 `inventoryManage.currency` 是 **CNY**；自定义报表顶层 `currency` 是 USD（可配置）。
   货币型指标的币种写进 `index_comment`，跨币种不得直接相减。

### 已核对存在的常用字段（节选，供 `desired_indices` 填写）

> 下表「自定义报表宽表」指赛狐 489 字段宽表；查询与字段核对由 custom-report-analysis
> 技能承接（本技能不查询宽表，仅引用字段口径写 `index_formula` / `index_comment`）。

| 指标 | 字段 | 出现在 |
|---|---|---|
| 销售额 | `productSales` | `get_profit_report_by_*` |
| 销售额（报表） | `salePrice` / `revenueAmount` | 自定义报表宽表 |
| 订单数 | `orderNum` | 自定义报表宽表 |
| 毛利率 | `grossProfitRate` | `get_profit_report_by_shop` / `_msku` |
| 广告花费 | `cpcCost`（负数费用） | `get_profit_report_by_*` |
| 广告转化率 | `adConversionRateCpc` | 自定义报表宽表（489 字段，字段字典 06 确认） |
| ACOS | `acos` / `spAcos` / `sbAcos` / `sdAcos` | 自定义报表宽表 |
| 退款率 | `refundRate` | 自定义报表宽表 / 利润报表 |
| 周转天数 | `turnoverDaysMonth` | 自定义报表宽表 |
| 可售天数 | `availableDays` | 自定义报表宽表 |
| 补货建议量 | `suggestNumSum` | 自定义报表宽表 |
| 汇率 | `rate` / `currencyList` | `get_profit_report_by_msku`（171 字段，实测确认） |

> ⚠️ **示例条目里的公式一律留空**（`formula: ""` + `formula_status: needs_human`），
> 等业务方确认语法后回填。这是本技能最关键的诚实边界。

## 四、自算指标的公式约定（仅作 `index_comment` 文字说明，不是 `index_formula` 语法）

以下为**业务算法说明**，供写入 `index_comment` 供人阅读；**不是** `index_formula` 的合法语法：

| 指标 | 业务算法 |
|---|---|
| 客单价 | 销售额 / 订单数 |
| 广告成本占比 | \|广告花费\| / 销售额 × 100（花费为负，须取绝对值） |
| 日均销量 | `salesVelocityMonth` / 30 |
| 库龄占比 | `invAge271To365Days` / `totalInventory` × 100 |

> 有现成官方口径的一律直接用官方字段（如 `refundRate`、`turnoverDaysMonth`、
> `availableDays`、`suggestNumSum`），**不自算** —— 自算会与业务方对不上账。
> 分母为 0 时不得输出 `inf`/`NaN`，标「无数据」或「不适用」。

## 五、货币 / 时区在注释里的落地

`index_comment` 建议固定包含三段：

```
口径：<自算算法或“直接采用赛狐 XXX 字段”>
利润口径：settlement（结算）/ shipment（发货）—— 二选一，不得混用
币种与时区：币种 USD（或注明确已折算，汇率 rate=<值> 日期 <日期>）；业务日以 bizDay 为准
```

跨站点（US/EU/JP）时区不同，「昨日」边界不同；涉及时间窗的指标必须在注释里写明口径。
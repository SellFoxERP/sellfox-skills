# 官方指标树基线与字段核对（index-tree-baseline.md）

> 解析 `get_custom_report_stat_index_list` 时读本文件。
> 来源：对赛狐 MCP 网关的实测整理。**`mappedIndex` 取值以实测为准。**

## 一、30 节点指标树（一级指标）

`get_custom_report_stat_index_list` 返回官方认可的可统计指标清单，直接决定日报能写哪些指标。
实测一级指标如下：

| 一级指标 | mappedIndex | 数据源 | 下钻子指标 |
|---|---|---|---|
| 销售额 | `sales_amount` | 订单数据 | 订单数 → 待支付订单数 |
| 利润 | `profit` | 财务数据 | 毛利率 → 成本金额 |
| 访客数 | `visitor_count` | 流量数据 | 新客数 → 老客数 |
| 转化率 | `conversion_rate` | 流量数据 | 加购率 → 收藏率 |
| 客单价 | `average_order_value` | 订单数据 | 平均件数 → 商品件数 |
| 退款金额 | `refund_amount` | 售后数据 | 退款订单数 → 退款率 |
| 广告花费 | `advertising_spend` | 广告数据 | 广告销售额 → 广告投入产出比(ROAS) |
| 库存数量 | `inventory_quantity` | 库存数据 | 可售库存 → 锁定库存 |
| 发货数量 | `shipped_quantity` | 物流数据 | 配送时效 → 准时送达率 |
| 评价数量 | `review_count` | 评价数据 | 好评率 → 差评数量 |

> 💡 指标树里有 **ROAS、好评率、加购率、准时送达率** —— 这些是**现成口径**，
> 日报/周报直接用，不要自己从头算（自算会与业务方对不上账）。

## 二、两个 `op_type` 的差异

`get_custom_report_stat_index_list` 的 `op_type`：

| op_type | 含义 | 用途 |
|---|---|---|
| `1` | 新增自定义指标可用目录 | 生成「新增」提案时用 |
| `2` | 编辑自定义指标可用目录 | 生成「更新」提案时用 |

**两次都要取**（可用指标集合可能不同），取并集作为基线。

## 三、解析注意（结构未经实测）

本技能脚本用 `client.call_raw()` 取业务层 `{code, msg, data}`（`call()` 在空数据时返回 `None`，
且会把列表包成 `{"data": [...]}`，不便判断）。`data` 可能形态：

- **list**：直接就是节点数组；
- **dict**：
  - `data.rows` / `data.list` / `data.records` → 节点数组；
  - `data.children` → 树形子节点；
  - 其它键 → 按需递归。

解析规则：优先取数组；取不到就递归扫描 `dict` 里第一个「元素为 dict 且含 `mappedIndex`
或 `indexName`/`name` 的 list」。**全部失败则标注「⚠️ 官方目录结构未能解析，需人工核查」**，
不得臆造节点。

## 四、指标树与自定义指标的边界

- 指标树是**官方目录**（只读基线），`add/update_custom_report_index` 写的是**自定义指标**。
- 自定义指标的 `index_formula` 里**可以引用**指标树中的现成口径（如 ROAS），
  但**引用记号语法未文档化** → 必须人工确认（见 `references/formula-and-fields.md`）。
- 本技能的差异比对是「公司期望指标（`desired_indices`）」对照「现有自定义指标」，
  指标树只作为**命名与口径的官方参照**，不直接与自定义指标逐节点比对。
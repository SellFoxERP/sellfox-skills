# 日报字段地图（field-map.md）

> 字段名**以此为准**，不凭记忆书写。权威来源：`_shared/FIELD_DICTIONARY.md`（字段 → 工具）；
> 工具参数以网关当次 `search` 返回的契约为准。
> SKILL.md 需要核对字段时读本文件。

## 目录

- [一、六个数据源与列表字段名](#一六个数据源与列表字段名)
- [二、L1 结论层字段](#二l1-结论层字段)
- [三、STEP 01 账户 / 订单](#三step-01-账户--订单)
- [四、STEP 02 销售 / 流量](#四step-02-销售--流量)
- [五、STEP 03 广告](#五step-03-广告)
- [六、STEP 04 库存](#六step-04-库存)
- [七、STEP 05 转化](#七step-05-转化)
- [八、STEP 06 售后](#八step-06-售后)
- [九、STEP 07 政策](#九step-07-政策)
- [十、币种与陷阱](#十币种与陷阱)

---

## 一、六个数据源与列表字段名

| # | 工具 | 列表字段 | 分页 | 本技能用途 |
|---|---|---|---|---|
| 1 | `get_shop_page_list` | `data.rows` | 页码（硬上限 20） | 店铺清单（可缓存） |
| 2 | `get_profit_report_by_shop` | **`data.reportList`** | 页码 | 店铺级利润 → 结论速览 |
| 3 | `get_custom_report_page_list` | `data.rows` | 页码 | 489 字段宽表 → 分项明细主力 |
| 4 | `get_order_page_list` | `data.rows` | 页码 | 订单异常下钻样本 |
| 5 | `get_sp_campaign_list` | **`data.itemList`** + `data.nextToken` | **游标** | 广告活动状态 |
| 6 | `get_multiplatform_sale_stats` | `data.rows` | 页码（`page_size<=1000`） | 跨平台销量 |

> ⚠️ 用 `rows` 解析利润报表会拿到空结果且不报错。`sellfox_client` 已按工具自动识别，
> 显式传入时务必用 `list_key="reportList"` / `"itemList"`。

### 关键请求参数（实测确认）

| 工具 | 必填参数 | 日期格式 |
|---|---|---|
| `get_profit_report_by_shop` | `start_date`、`end_date`、`date_type=2`（按日） | **`yyyyMMdd`** |
| `get_custom_report_page_list` | `report_id`、`start_date`、`end_date` | **`yyyy-MM-dd`** |
| `get_order_page_list` | `date_type`（如 `purchase`）、`date_start`、`date_end` | `yyyy-MM-dd HH:mm:ss` |
| `get_sp_campaign_list` | `shop_id` | 游标 `next_token` |
| `get_multiplatform_sale_stats` | `currency`、`group_type`、`page_no`、`page_size`、`stat_time_type`、`type` | `yyyy-MM-dd` |

---

## 二、结论速览字段

来源均为 `get_profit_report_by_shop` 的 `reportList[]`：

| 指标 | 字段 | 说明 |
|---|---|---|
| 销售额 | `productSales` | 店铺级汇总 |
| 销量 | `salesNum` | 注意：与自定义报表的 `saleNum` 拼写不同 |
| 毛利 | `grossProfit` | 口径由 `profit_caliber_type` 决定 |
| 毛利率 | `grossProfitRate` | 也可自算 毛利/销售额 |
| ROI | `roi` | — |
| 退款销售额 | `refundSales` | — |
| 广告花费（扣减） | `cpcCost` | **为负值（费用）** |
| 广告成本占比 | `cpcCostRate` | — |
| 仓储费 | `storageFee` | — |
| 业务日 | `bizDay` | `yyyyMMdd`，跨站点业务日边界以此为准 |

---

## 三、STEP 01 账户 / 订单

**自动化可得**（均来自 `get_order_page_list.rows[]`，40 字段）：

| 字段 | 含义 |
|---|---|
| `amazonOrderId` | 订单号 |
| `orderStatus` | 订单状态 |
| `orderTotalAmount` / `orderTotalCurrency` | 金额 / 币种 |
| `isReturnOrder` | 是否退货单 |
| `refundStatus` / `refundDate` | 退款状态 / 日期。`refundStatus` 为字符串枚举：线上实测 `"0"`=非退款，非 `"0"`=退款——**勿按真值判断**（`"0"` 在 Python 中为真） |
| `isBuyerRequestedCancel` | 买家请求取消 |
| `orderFlag` / `comment` | 订单标记 / 备注 |
| `settlementStatus` | 结算状态 |
| **`buyerEmail` / `buyerName`** | **PII —— 写入外部文档前必须脱敏** |

**退款/退货计数**优先用宽表字段（比订单抽样更准）：`refundNum`、`refundPrice`、`returnSaleNum`。

**MCP 无数据源、必须人工核查**：Account Health、绩效通知、停售/抑制**原因**（仅能用在线产品
`onlineStatus`：`Active`/`inActive`/`delete` 近似）、买家消息 / 工单。

---

## 四、STEP 02 销售 / 流量

宽表 `get_custom_report_page_list.rows[]`（一行一 MSKU）：

| 指标 | 字段 | 备注 |
|---|---|---|
| 销量 | `saleNum` | — |
| 销售额 | `salePrice` / `revenueAmount` | 与 `productSales` 口径是否一致需业务确认 |
| 订单数 | `orderNum` | — |
| 客单价 | `avgSalePrice` | 或 `salePrice / orderNum` 自算 |
| 会话 | `sessions`、`sessionApp`、`sessionWeb` | — |
| 浏览量 | `pageViews` | — |
| 转化率 | `conversionRate`、`orderConversionRate` | 报表现成口径 |

**基线窗口字段（推荐方案 A，无需自拼时间窗）**：
`sevenSaleNum`（近 7 天）、`thirtySaleNum`（近 30 天）；
另有 `fourteenSaleNum` / `sixtySaleNum` / `ninetySaleNum`、`avgSaleNum`、`dayNum`。
（`threeSaleNum` / `sevenSaleNum` / `fourteenSaleNum` / `thirtySaleNum` / `sixtySaleNum` / `ninetySaleNum` 均为真实窗口字段，见 `_shared/FIELD_DICTIONARY.md`。）

> 日均基线 = `sevenSaleNum / 7`，偏离度 = `(saleNum - 日均) / 日均`。

---

## 五、STEP 03 广告

宽表字段（花费/转化）：

| 指标 | 字段 |
|---|---|
| 广告花费 | `adCost`、`adSpCost`/`adSbCost`/`adSdCost` |
| ACOS | `acos`、`spAcos`/`sbAcos`/`sdAcos` |
| ROAS | `roas` |
| CPC | `cpc` |
| 广告曝光 / 点击 / 点击率 | `adImpressions`、`adClicks`、`adClickRate` |
| 广告订单 | `adOrderNum` |
| 广告销售额 | `adSalePrice` |
| **广告转化率** | **`adConversionRateCpc`** ⭐ 字段字典 06 确认（样例 2.33），可用于 ACOS 归因 |

活动维度 `get_sp_campaign_list.itemList[]`：`campaignId`、`name`、`state`、`budget`、
`targetingType`（`auto`/`manual`）、`servingStatus`、`campaignSite`、`adjustments`、`lastUpdatedDate`。

> ⚠️ `state` 取值仅用实测确认的 **`enabled` / `paused` / `archived`**。
> `servingStatus` 字段真实存在（实测值 `CAMPAIGN_PAUSED`），但 **`BUDGET_CAPPED` 未经实测验证，
> 不得作为诊断规则的判定条件**（仅可作人工提示）。

---

## 六、STEP 04 库存

宽表字段：

| 指标 | 字段 |
|---|---|
| 可售天数 | `availableDays`、`fbmAvailableDays` |
| 在途 | `inboundWorking`、`inboundShipped`、`inTransit`、`inboundReceiving` |
| 库龄 7 桶 | `invAge0to30Days` … `invAge365PlusDays` |
| 周转 | `turnoverDaysMonth`、`turnoverRateMonth`、`isrMonth`、`salesVelocityMonth` |
| 补货建议量 | `suggestNumSum`（**直接用赛狐官方口径**） |
| 多仓 | `localAvailableQty`、`overseaAvailableQty`、`fbaInventory`、`awdAvailable` |
| FBA 总量 | `fbaTotalInventory`（库龄占比分母） |
| 安全库存 | `domesticSafeStock`、`overseaSafeStock` |

> 周转天数、ISR、补货建议量赛狐**已算好** —— 不自造算法，避免与业务方对不上账。

---

## 七、STEP 05 转化

| 指标 | 字段 |
|---|---|
| 会话 | `sessions`、`sessionApp`、`sessionWeb`、`sessionsB2b` |
| 转化率 | `conversionRate`、`orderConversionRate` |
| 购物车占比 | `buyBoxPercent`、`buyboxPrice` |
| 自然转化率 | `naturalConversionRate` |
| 广告转化（CPC） | `adConversionRateCpc` |

> STEP 02 与 STEP 05 数据重叠：02 看结果（销售额/订单），05 看过程（流量/转化/购物车）。

---

## 八、STEP 06 售后

| 指标 | 字段 |
|---|---|
| 退款数 / 率 / 金额 | `refundNum`、`refundRate`、`refundPrice` |
| FBA/FBM 退款数 | `refundNumFba`、`refundNumFbm` |
| 退货数 / 率 | `returnSaleNum`、`returnSaleNumRate` |
| 可售/不可售退货 | `buyableReturnNum`、`unBuyableReturnNum` |
| 换货 | `replaceSaleNum` |
| 评论数 / 差评数 / 好评率 | `reviewNum`、`lowReviewNum`、`reviewRate` |
| 评分 | `rating`（字符串，需转 float）、`ratingCount` |

> ❌ 评论**原文**拿不到 —— 只有聚合指标。

---

## 九、STEP 07 政策

`get_custom_report_page_list` 中另有赔偿/延期字段（**仅存在于宽表**，不在 MSKU 利润报表）：
`reimbursementAmount`、`reversalReimbursementAmount`、`warehouseDamageAmount`、`warehouseLostAmount`、
`missingFromInboundAmount`、`deferredAmount`。

MCP **无**任何政策 / 合规 / 申诉 / 绩效工具 → STEP 07 必须人工。

---

## 十、币种与陷阱

| 位置 | 币种 | 说明 |
|---|---|---|
| `get_custom_report_page_list.currency` | USD（可配置） | 报表币种；本技能用 `report.currency` 显式指定 |
| `get_online_product_page_list.currency` | **USD** | 售价口径 |
| `get_online_product_page_list.inventoryManage.currency` | **CNY** | **成本口径** |
| `get_profit_report_by_*` 的 `currency` 参数 | 可选 | 可按指定币种折算 |
| `rate` / `currencyList` | 汇率 | 出现在 `get_profit_report_by_msku`（171 字段） |

> **售价（USD）与库存成本（CNY）不得直接相减**；跨站点聚合必须显式标注币种、汇率来源与日期。
> 本技能不混用 `get_online_product_page_list` 的成本字段做利润计算。
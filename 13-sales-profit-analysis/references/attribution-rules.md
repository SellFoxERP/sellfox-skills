# 归因规则：日序列四查 + 五子链 + L4 方向

> 先定位：`Δ毛利` = 收入侧 Δ + 支出侧 Δ（用桥接七项，见 [field-map.md](field-map.md)），
> 挑吃掉最多的进对应子链。**金额问题先于比率问题**。
> 置信分级：🟢 两期同字段对照 ｜ 🟡 单期单字段或口径变体 ｜ ⚪ 契约无源，只列假设。
> **⚪ 不得升成 🟡 凑归因条数；顺着"利润下滑了"的前提硬找下滑是反模式。**

## 一、日序列四查（变化归因类默认做；只问总额或明细可省）

一行聚合数字可以在算术上完全正确，却仍给出错结论。前两项只有日序列能看见，也最常翻车：

| 查什么 | 看什么 | 被聚合掩盖成什么样 |
|---|---|---|
| **单日贷项** | 费用字段出现**正值**的那一天（广告费/仓储费为正＝返点·冲回·调整入账） | 基期被这笔贷项抬高或压低 → **Δ 幅度失真**。实测：某店基期单日一笔 +273.6 广告贷项，使区间显示的"改善 +102"实为约 +376。落点见费用子链「调整类贷项入账」 |
| **断点 vs 渐变** | 某项费用或销量从某天起持续为 0 / 跳变 | "广告费降 90%"读起来像优化，实为某日起**连续多日彻底停投**；若同主体多店同日归零，指向账户级动作 → 落点见「费用停投断点」，建议是"先确认谁停的"而不是"怎么恢复" |
| **费用集中入账** | 赔偿、清算、退货处理费是否只落在少数几天 | 摊进整个窗口被读成"持续发生"，实际是几笔事件，应按事件核对而非按趋势归因 |
| **结算滞后** | 窗口尾部 2–3 天日均销售额/销量是否显著低于前段 | 尾部未结完会低估本期。无塌陷则"改善"方向成立；有塌陷须声明右端不完整或改用更早基期 |

反向校验：**日分段各项加总 == 聚合行同字段**（容差 ≤0.01）。不等就是漏页、漏分段或币种混行
——先修取数，再谈结论。

## 二、五子链根因表

### 销售子链

| 根因 | 证据 | 置信 |
|---|---|---|
| 销量下滑（客单未动）/ 客单跌（销量未动） | `salesNum` `productSales`，客单 = `productSales÷salesNum`【自算】 | 🟢 |
| 广告缩量（掉量的主因常在这） | `cpcCost` `cpcSales`、`adSpOrderNum`/`cpcSalesNum` 对比 `salesNum` 总降幅——算出"销量下滑里有多少是广告单减少" | 🟢 |
| 促销让利加深 | `promotionValue` `promotionRefund`（与 `promotionRefundSales` 值一致，不可相加） | 🟢 |
| 退款冲销销售额 | `refundSales` `refundNum` | 🟢 |
| 结构性稀释（低率品占比涨） | 按 `grossProfitRate` 分箱 + `parentAsinList` | 🟡 |
| 断货 / 流量 / 转化 / BuyBox / 评分 / 竞品压价 | ⚪ 无数据源（SKILL.md 边界）→ 写"利润表无法证实，需后台核对" | ⚪ |

### 成本子链

| 根因 | 证据 | 置信 |
|---|---|---|
| 采购/头程涨价（销量平） | `purchasePcFeeBatch` `headTripPcFeeBatch` + 两个 `*Rate`；order 侧拼写 `batchPurchasePcFee`/`batchTripPcFee` | 🟢 |
| 两条链或层级切换背离 | `purchasePcFee` vs `purchasePcFeeBatch`；`orderMCFIFO` vs `orderMCFIXED` | 🟡 |
| 批次→平均兜底下移 | `avgPurchasePcFeeBatch` `avgHeadTripPcFeeBatch` 被启用 ∧ 批次项空（新品无批次属正常） | 🟡 |
| 退货/盘点/移除吃成本 | `buyerReturnFIFOFeeSell`/`UnSell` `inventoryAdjFIFOFeeSell` `removalFIXED` `disposalFIXED` | 🟢 |
| 签收差异 | `receivedDiffPurchase` `receivedDiffHeadtrip` `receivedDiffNum` | 🟢 |
| 成本口径开关整列覆写 | ⚪ 报表层不可见 → 标"需核后台成本设置" | ⚪ |

### 费用子链

| 根因 | 证据 | 置信 |
|---|---|---|
| 佣金 / FBA 配送与计重涨 | `commission` `fbaFulfillmentFee` `fbaWeightBasedFee` `fbaTransportationFee` | 🟢 |
| 仓储涨（常规/长期/超量/卫星仓） | `storageFee` `storageFeeReport` `storageFeeDiff` `storageRenewalBilling` `fbaOverageFee` `starStorageFee`；**销量降而仓储升 = 周转恶化信号** | 🟢 |
| 退货入仓/标签/退款管理费 | `fbaReturnFee` `returnPostageFee` `refundManagementFee` | 🟢 |
| 赔偿与追回 | `compensationFee`（正）`compensatedClawback`（负）`fbaInventoryCompensation` `a2zGuaranteeRecovery` `adjCompensationNum`；细看 `get_amazon_reimbursement_report` | 🟢 |
| **广告三源分歧** | `cpcCost`（来源计算）vs `cpcSettlementCost`（结算报告）vs `cpcAdExpensesCost`（账单），差额 `cpcCostSettlementDiff`/`cpcCostAdExpensesDiff`；一个结论只认一个源并写明 | 🟡 |
| 订阅/固定费/销毁移除入库 | `subscriptionFee` `fixedClosingFee` `monthlyFixedFee` `fbaDisposalFee` `fbaRemovalFee` `fbaInboundConvenienceFee` `labelingFee` | 🟢 |
| 税 / 测评 / 自定义费 | `orderTax` `marketplaceWithheldTax` `productSalesTax` `amazonVatServiceFee` `tcs`；`customFee` `evaluationCapital`（长尾税项先查字典） | 🟡 |
| **调整类贷项入账**（不是经营改善） | 某天费用/成本字段**取正值**＝冲回·返点·账期调整：`compensationFee`（正）`compensatedClawback`（负）`recoveryAmount` `guaranteeClaim` `reimbursedAmount` `liquidationsBrokerageFee` `feeAdjustment` `revenueAdjustment` `adjustmentAmount` `subscriptionFeeCorrection`。**基期含此类单日贷项时，Δ 必须按剔除后口径重算，并把"账面 Δ / 剔后 Δ"两个值都报出来**（实测：一笔单日贷项可使区间显示的改善被低估三倍以上） | 🟢 |
| **费用停投断点** | 日序列里某项费用自某天起**持续为 0** 且对应单量同步归零（`cpcCost` 连续为 0 + `adSpOrderNum`/`cpcSalesNum` 归零）；同主体多店**同日**归零 → 判为账户级动作（预算·授权·报表拉取），标"需人工确认是否有意停"，**不得写成"成本下降带来改善"** | 🟢 |

### 退款子链

| 根因 | 证据 | 置信 |
|---|---|---|
| 退款额涨 ∧ 退款量涨 / 退货恶化 | `refund` `refundSales` `refundNum` `returnNum` `returnRate` | 🟢 |
| **分母效应**（退款率涨 ∧ `refund` 平 ∧ 销售额跌） | 归销售侧，不算退款恶化 | 🟢 |
| 退货残值损失 | `returnSellAbleNum` vs `returnUnsellAbleNum`——**不可售占比高＝实损**（实测曾达 76%），不是"暂存" | 🟢 |
| 拒付 vs 退款 | `refundCommission`＝信用卡拒付收入、`commissionExpend`＝拒付支出（不是佣金退/支） | 🟢 |
| 延期结算未到账 | order 独有：`transactionStatus`=DEFERRED + `deferredDays` `deferredReason` `estimatedSettlementTime` `settlementStatus` `postedDate` | 🟢 |
| 退货**原因**归类 | ⚪ 利润表无原因文本 | ⚪ |

### 汇率子链

`rate` 变而原币未动 → 标「折算差异非经营变化」，不锁汇率；`currency` 出现切换点 → 币种切换，
不改结算币种；跨币种合并不可解释 → 🔴 取数失误，回门 2 重取。原币侧字段
`fbaSalesOrigin`/`fbmSalesOrigin` 与折后侧不可并列混算。

## 三、L4 方向（每条归因给的建议动作）

规模 vs 价格 → 换窗口复取；成本涨 → 复取两条链并标"需核到货批次价"；仓储涨 → 分常规/长期/超量；
退款涨 → 分「钱」与「量」+ 不可售占比；广告 → **先比单件毛利率 vs ACoS**（毛利率 < ACoS 时
砍广告是数学正确的，恢复投放的前提是抬单件毛利或换低 ACoS 词，不是全线回补）；延期结算 →
`_by_order` 带 `settlement_status="Open"`；净利 → 明列扣减项做【推算】（并声明契约无净利字段）。

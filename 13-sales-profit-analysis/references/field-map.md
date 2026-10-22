# 字段地图（桥接、符号方向、弃用与名不符实）

> grounding 说明：本表工具、字段、参数、返回类型与哨兵值均经真实 action 实测
> （月/日两种粒度、两期对照、多店多站点）；契约状态仍为 `documented_unverified`。
> 与 `_shared/FIELD_DICTIONARY.md` 的关系：该字典是历史参考索引；本表是本技能
> 依赖字段的**实测增量**，冲突时以当轮契约与实际响应为准。

## 一、桥接七项 + 常用单项（字段名经实测）

```
桥接（grossProfit 的加总项，符号照原值相加）：
  orderIncome  收入｜orderExpend 平台与配送（含 commission 佣金、fbaFulfillmentFee FBA配送费）
  serviceFeeExpend 服务与仓储（含 storageFee 月仓储、storageRenewalBilling 长期仓储、fbaOverageFee 超量）
  refund 退款（含 refundSales 销售额退回、refundManagementFee 退款管理费）
  purchasePcFeeBatch 采购（先入先出）｜headTripPcFeeBatch 头程（先入先出）｜cpcCost 广告
常用单项：commission fbaFulfillmentFee fbaWeightBasedFee storageFee storageRenewalBilling
  cpcCost cpcSettlementCost cpcAdExpensesCost cpcCostAdExpensesDiff cpcSpCost cpcSales cpcSalesRate
  refundSales promotionValue returnNum returnSellAbleNum returnUnsellAbleNum compensationFee
  fbaInventoryCompensation purchasePcFee headTripPcFee avgPurchasePcFeeBatch roi grossInvestment
维度：shopInfos[].shopId（顶层 shopId 恒 null）mskuList skuList asinList parentAsinList
  bizDay bizDate currency rate openDateList firstSaleDateList
```

**成本层级**（一个结论只认一层，认哪层写进口径行）：
批次 `purchasePcFeeBatch`/`headTripPcFeeBatch` → 平均 `avgPurchasePcFeeBatch`/`avgHeadTripPcFeeBatch`
→ 固定方式 `purchasePcFee`/`headTripPcFee`。

**符号以实测为准（线上曾见正号存储——成本费用全为正值；别假设"成本类都是负数"）**：

```
符号约定两态都实测过：负号存储（成本费用类为负：orderExpend serviceFeeExpend refund
  purchasePcFeeBatch headTripPcFeeBatch cpcCost commission）与正号存储（同族字段全为正）；
回收与赔偿类正：compensationFee recoveryAmount guaranteeClaim reimbursedAmount liquidation
               buyerReturnFIFOFeeSell inventoryAdjFIFOFeeSell（退货/盘点的残值回收是【加】进毛利的）
→ 桥接只能按原符号代数和；脚本按「主符号方向」判定贷项日（该字段两期全部行求和的主符号，
  取值符号与主符号相反且非零的那天 = 冲回/调整），不假设成本恒负
  （处理见 attribution-rules.md「调整类贷项入账」）
```

**金额与率值的类型**：契约文档称字符串（含负数与小数），线上实测亦见 **JSON number**、
`bizDay` 为 **int**——解析按 `num()` 两种都收；null/省略/解析失败 → None，绝不当 0；
日期键全链路归一为 `str` 再排序。

**契约自带组合（口径原文即定义，别自己拼）**：

```
orderIncome       = 销售额 + 买家运费 + 礼品包装费 + 监管费 + COD收费
refund            = 销售额退回 + 数字服务费退款 + 佣金退款 + 退款管理费 + FBA配送费退款
                    + 买家运费退款 + 促销折扣退款 + 税费退款 + 其他退款
customFee         = 测评费 + 店铺其他费 + 产品其他费 + 固定费用 + 每月固定费用
orderMCFIFO/FIXED = 采购成本 + 头程费用（先入先出 / 固定方式）
get_order_detail：  totalRevenue = 产品金额 + 退款 + 促销折扣 + 买家运费
                    totalCost    = FBA运费 + 销售佣金 + 其他费 + 采购 + 头程 + fbm运费 + 测评 + 税费
orderExpend / serviceFeeExpend 各含十余项（构成读字段描述）→ 用桥接组合项定位，别下钻"廿几项之和"
```

## 二、弃用（契约标「（弃用）」者不得作证据）

`disposalFee`/`removalFee` → 改用 `fbaDisposalFee`/`fbaRemovalFee`；order 侧另有十余项弃用字段，
取数前以契约标注为准。

## 三、名不符实（易错字段）

| 字段 | 实际含义 |
|---|---|
| `revenue` | 清算收入（总收入是 `orderIncome`） |
| `refundCommission` | 信用卡**拒付收入**（不是佣金退） |
| `commissionExpend` | 信用卡**拒付支出**（order 侧） |
| `giftWrapCommission` | 其他-礼品包装费手续费 |
| `fbaSales`/`fbmSales` | 「根据配置含税不含税」，跨店比较前先核对配置 |
| `disbursementCorrection` | 不计入利润 |
| `productSales=0` 行的 `grossProfitRate=0`/`roi=-1`/`profitRate="0E-7"` | 零分母**哨兵值**，非真值 → 剔除出率值与排名 |

## 四、跨工具不可混算

- 产品分析 `saleNum`/`salePrice` ≠ 利润报表 `salesNum`/`productSales`；
- `get_multiplatform_sale_stats` 自述 `not_for` 利润、毛利、广告归因；
- 产品分析 `adTotalCost` = 利润报表 `cpcSpCost`（账单口径）≠ `cpcCost`（结算口径），
  两层不相加、不同表（见 workflow.md「两个主力」）。

## 五、专项工具字段（按需）

- `get_order_detail`：`totalRevenue`＝产品金额+退款+促销折扣+买家运费；`totalCost`＝FBA运费+
  销售佣金+其他费+采购+头程+fbm运费+测评+税费；`orderProfit`；`saleProfitRate`。行含买家信息，
  外发前 PII 脱敏。
- `get_fba_shipping_head_fee_page_list`：头程分摊明细。
- `get_amazon_reimbursement_report`：赔偿细看（金额核对用）。
- 入参一律以当轮契约为准。

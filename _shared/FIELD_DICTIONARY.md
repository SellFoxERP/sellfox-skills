# 字段字典与数据源索引（FIELD_DICTIONARY.md）

> 本文件是历史演示环境字段的精选参考索引，**不是全量字段表，也不是生产口径认证**。
> 查询、参数及字段类型以当前 MCP 契约为准；遇到 output_validation 差异应保留原值并说明，不能因本表未列出就判定字段不存在。

---

## 目录

- [一、三个宽表工具对比](#一三个宽表工具对比)
- [二、列表字段与分页类型（按工具）](#二列表字段与分页类型按工具)
- [三、标识与维度字段](#三标识与维度字段)
- [四、销售额 / 订单 / 客单价](#四销售额--订单--客单价)
- [五、利润 / 成本 / ROI](#五利润--成本--roi)
- [六、平台费用（亚马逊）](#六平台费用亚马逊)
- [七、广告](#七广告)
- [八、流量 / 转化](#八流量--转化)
- [九、评论 / 评分](#九评论--评分)
- [十、BSR / 类目](#十bsr--类目)
- [十一、退款 / 退货](#十一退款--退货)
- [十二、库存总量 / 构成](#十二库存总量--构成)
- [十三、本地仓 / 海外仓](#十三本地仓--海外仓)
- [十四、库龄](#十四库龄)
- [十五、周转 / 补货](#十五周转--补货)
- [十六、库存资金占用](#十六库存资金占用)
- [十七、AWD](#十七awd)
- [十八、赔偿 / 差异](#十八赔偿--差异)
- [十九、结算 / 延期](#十九结算--延期)
- [二十、Listing 属性](#二十listing-属性)
- [二十一、库存管理子对象 inventoryManage](#二十一库存管理子对象-inventorymanage)
- [二十二、订单 / 广告任务 / 店铺（工具级）](#二十二订单--广告任务--店铺工具级)
- [二十三、多平台广告字段](#二十三多平台广告字段)
- [二十四、数据源速查表](#二十四数据源速查表)
- [二十五、币种陷阱](#二十五币种陷阱)

---

## 一、三个宽表工具对比

| 工具 | 单行字段数 | 定位 | 适用场景 | 列表字段 |
|---|---|---|---|---|
| `get_custom_report_page_list` | **489** | 全指标宽表（本地/海外仓、库龄、流量、广告、评论） | 日报、库存、利润、Listing 体检 **主力** | `data.rows` |
| `get_profit_report_by_msku` | **171** | 财务深度（亚马逊费用逐项拆解） | 利润诊断、成本归因 | `data.rows`（标准化分页） |
| `get_online_product_page_list` | **79** + `inventoryManage`(41) | 在线产品 + FBA 库存 | Listing 体检、FBA 库存 | `data.rows` |

> 💡 **选型口诀**：要**广**用自定义报表；要**深**用 MSKU 利润报表；要 **Listing 属性**用在线产品。

---

## 二、列表字段与分页类型（按工具）

| 工具 | 列表字段 | 分页类型 | 备注 |
|---|---|---|---|
| `get_shop_page_list` | `data.rows` | 页码（硬上限 20） | `id`/`name`/`marketplaceId` 等 |
| `get_order_page_list` | `data.rows` | 页码 | 40 字段，含 PII |
| `get_online_product_page_list` | `data.rows` | 页码 | 79 字段 |
| `get_custom_report_page_list` | `data.rows` | 页码 | 489 字段宽表 |
| `get_profit_report_by_shop` | **`data.rows`**（标准化分页） | 页码 | 店铺级 |
| `get_profit_report_by_msku` | **`data.rows`**（标准化分页） | 页码 | 171 字段 |
| `get_profit_report_by_sku` | **`data.rows`**（标准化分页） | 页码 | — |
| `get_profit_report_by_order` | **`data.rows`**（标准化分页） | 页码 | 74 字段 |
| `get_sp_campaign_list` | **`data.rows`**（标准化分页）+ 游标 | **游标** | 广告活动 |
| `get_sp_keyword_list` / `get_sp_*_targeting_list` | `data.rows`（标准化分页）+ 游标 | 游标 | 投放 |
| `get_sb_*_list` | `data.rows`（标准化分页）+ 游标 | 游标 | SB 全套 |
| `get_ad_download_task_page_list` | `data.rows` | 页码 | `downloadUrl` 所在 |
| `get_custom_report_index_page_list` | `data.rows` | 页码 | 自定义指标 |

> ⚠️ **用 `rows` 解析利润报表会拿到空结果且不报错** —— 最常见的隐性错误。

---

## 三、标识与维度字段

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `asin` | ASIN（可能为 `[{key:...}]` 形式） | 自定义报表、在线产品 |
| `parentAsin` | 父 ASIN | 自定义报表、在线产品 |
| `sku` | SKU | 自定义报表、在线产品 |
| `msku` | 卖家 SKU | 自定义报表 |
| `fnsku` | FNSKU | 自定义报表、在线产品 |
| `spu` | SPU | 自定义报表、利润报表、商品分析 |
| `shopId` | 店铺 ID | 多工具 |
| `marketplaceId` | 站点 ID（如 `US`） | 自定义报表、订单、在线产品 |
| `title` | 标题 | 自定义报表、在线产品、订单、商品分析、Temu 报表 |
| `mainImage` | 主图 URL | 自定义报表、在线产品、商品分析 |
| `brand` | 品牌 | 自定义报表、在线产品 |
| `devId` | 开发员 | 自定义报表、在线产品 |
| `operatorId` | 业务员 | 自定义报表 |
| `openDate` | 上架日期 | 自定义报表、在线产品、商品分析 |
| `currency` | 币种（顶层，多为 USD） | 多工具 |
| `bizDay` | 业务日（`yyyyMMdd`） | `get_profit_report_by_shop` / `_msku` |
| `nowDate` | 当前日期 | 自定义报表 |
| `dayNum` | 天数 | 自定义报表 |

---

## 四、销售额 / 订单 / 客单价

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `productSales` | 销售额 | `get_profit_report_by_shop` / `_order` / `_msku` |
| `fbaSales` / `fbmSales` | FBA / FBM 销售额 | 利润报表 |
| `saleNum` | 销量 | 自定义报表、在线产品 |
| `salesNum` | 销量 | 利润报表（店铺/订单/MSKU） |
| `salePrice` | 销售额 | 自定义报表 |
| `revenueAmount` | 销售额 | 自定义报表 |
| `orderNum` | 订单数 | 自定义报表 |
| `avgSalePrice` | 客单价 | 自定义报表 |
| `orderTotalAmount` | 订单总额 | 自定义报表、订单 |
| `saleNumFba` / `saleNumFbm` | FBA / FBM 销量 | 自定义报表 |
| `sevenSaleNum` / `thirtySaleNum` | 近 7 / 30 天销量（现成窗口） | 自定义报表 |
| `productSaleDayOpenVo` | 按日销量明细 | `get_multiplatform_sale_stats` |

> ✅ `threeSaleNum` **是真实字段**（`get_custom_report_page_list`，近 3 天销量窗口；以本字典为权威）。
> 窗口字段全家桶：`threeSaleNum` / `sevenSaleNum` / `fourteenSaleNum` / `thirtySaleNum` / `sixtySaleNum` / `ninetySaleNum`（自定义报表），
> `day7SaleNum` / `day14SaleNum` / `day30SaleNum`（在线产品）。

---

## 五、利润 / 成本 / ROI

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `grossProfit` | 毛利 | 利润报表（店铺/订单/MSKU） |
| `grossProfitRate` | 毛利率 | `get_profit_report_by_shop` / `_msku` |
| `profitRate` | 利润率 | 自定义报表、在线产品 |
| `profitRateNet` | 净利率 | 自定义报表 |
| `profitPrice` | 利润额 | 自定义报表、在线产品 |
| `revenue` | 收入 | `get_profit_report_by_msku` |
| `grossIncome` | 总收入 | 利润报表（订单/MSKU） |
| `grossInvestment` | 总投入 | 利润报表（订单/MSKU） |
| `roi` | ROI | `get_profit_report_by_shop` / `_msku`、TikTok |
| `purchaseCost` | 采购成本 | 自定义报表、在线产品 |
| `avgPurchaseCost` | 平均采购成本 | 自定义报表 |
| `headTripPrice` | 头程价格 | 自定义报表 |
| `commodityCost` | 商品成本 | 自定义报表 |
| `purchasePcFee` | 采购费（利润扣减） | 利润报表（店铺/订单/MSKU） |
| `headTripPcFee` | 头程费（利润扣减） | 利润报表（店铺/订单/MSKU） |
| `currencyList` | 币种列表（汇损归因） | `get_profit_report_by_msku` ⭐ |
| `rate` | 汇率 | `get_profit_report_by_msku` / `_order` ⭐ |

---

## 六、平台费用（亚马逊）

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `commission` | 佣金 | 利润报表（店铺/订单/MSKU） |
| `fbaFulfillmentFee` | FBA 配送费 | 利润报表（店铺/订单/MSKU） |
| `fbmPostageFee` | FBM 运费 | `get_profit_report_by_msku` |
| `variableClosingFee` | 可变结算费 | 在线产品、MSKU 利润报表 |
| `fixedClosingFee` | 固定结算费 | `get_profit_report_by_msku` |
| `storageFee` | 仓储费 | `get_profit_report_by_shop` / `_msku` |
| `fbaLongTermStorageFeeAmount` | 长期仓储费 | 自定义报表 |
| `fbaOverageFeeAmount` | 超量仓储费 | 自定义报表 |
| `promotionValue` | 促销费 | `get_profit_report_by_shop` / `_msku` |
| `regulatoryFee` | 监管费 | `get_profit_report_by_msku` |
| `digitalServicesFeeAmount` | 数字服务费 | 自定义报表 |
| `subscriptionAmount` | 订阅费 | 自定义报表 |
| `totalFee` / `referralFee` / `fbaFees` / `perItemFee` | 各类费用汇总 | 在线产品 |

---

## 七、广告

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `adCost` | 广告花费 | 自定义报表、Walmart 报表 |
| `adSpCost` / `adSbCost` / `adSdCost` | SP / SB / SD 广告花费 | 自定义报表 |
| `cpcCost` | 广告花费（利润扣减，负值） | 利润报表（店铺/订单/MSKU） |
| `cpcCostRate` | 广告成本占比 | `get_profit_report_by_shop` / `_msku` |
| `cpc` | 单次点击成本 | 广告报表、自定义报表 |
| `cpa` | 单次获客成本 | 多平台广告报表 |
| `cpo` | 单次订单成本 | 自定义报表 |
| `acos` | ACOS | 多平台广告报表、自定义报表 |
| `spAcos` / `sbAcos` / `sdAcos` | SP / SB / SD 的 ACOS | 自定义报表 |
| `roas` | ROAS | 多平台广告报表、自定义报表 |
| `acoas` / `asoas` | 广告成本占销售额比 | 自定义报表 |
| `adImpressions` | 广告曝光 | 自定义报表 |
| `adClicks` | 广告点击 | 自定义报表 |
| `adClickRate` | 广告点击率 | 自定义报表 |
| `adOrderNum` | 广告订单数 | 自定义报表、Walmart 报表 |
| **`adConversionRateCpc`** | **广告转化率** | **`get_custom_report_page_list`** ⭐ 字段字典 06 确认（样例 `2.33`），ACOS 归因可用 |
| `adSalePrice` | 广告销售额 | 自定义报表 |

> ✅ `adOrderConversionRate` **是真实字段**（`get_custom_report_page_list`，489 字段；以本字典为权威）。
> 广告转化字段并存：`adConversionRateCpc` / `adConversionRateVcpm` / `adOrderConversionRate`（广告侧），
> `orderConversionRate`（订单侧）、`conversionRate`（会话侧）。归因时按需要选，不要宣称哪个「不存在」。
| `cost` | 花费 | TikTok / Mercado 广告 |
| `ctr` / `cvr` | 点击率 / 转化率 | 多平台广告报表 |
| `netRoas` / `netAcos` | 净 ROAS / 净 ACOS | Temu 报表 |

---

## 八、流量 / 转化

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `sessions` | 会话数 | 自定义报表 |
| `pageViews` | 浏览量 | 自定义报表 |
| `sessionApp` / `sessionWeb` | App / Web 会话 | 自定义报表 |
| `sessionsB2b` | B2B 会话 | 自定义报表 |
| `conversionRate` | 转化率 | 自定义报表、Shopee 报表 |
| `orderConversionRate` | 订单转化率 | 自定义报表 |
| `conversionRateB2b` | B2B 转化率 | 自定义报表 |
| `buyBoxPercent` | 购物车占比 % | 自定义报表 |
| `buyboxPrice` | 购物车价格 | 自定义报表 |
| `naturalConversionRate` | 自然转化率 | 自定义报表 |

---

## 九、评论 / 评分

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `rating` | 评分（字符串） | 自定义报表、在线产品 |
| `ratingCount` | 评论数 | 自定义报表、在线产品 |
| `reviewNum` | 评论数 | 自定义报表 |
| `lowReviewNum` | 差评数 | 自定义报表 |
| `reviewRate` | 好评率/评分 | 自定义报表 |
| `evaluationProductNum` | 评价商品数 | 自定义报表 |

---

## 十、BSR / 类目

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `bsrRank` | BSR 排名（含类目名、大类/小类） | 在线产品、商品分析 |
| `bigCategory` / `smallCategory` | 大类 / 小类名 | 自定义报表 |
| `bigCategoryRanking` / `smallCategoryRanking` | 大类 / 小类排名 | 自定义报表 |
| `fullCid` | 类目路径 | 自定义报表、在线产品 |
| `categoryNameList` | 类目名列表 | 商品分析 |

---

## 十一、退款 / 退货

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `refundNum` | 退款数 | 自定义报表、利润报表 |
| `refundRate` | 退款率 | 自定义报表、利润报表 |
| `refundPrice` | 退款金额 | 自定义报表 |
| `refundSales` | 退款销售额 | `get_profit_report_by_shop` / `_msku` |
| `refundNumFba` / `refundNumFbm` | FBA / FBM 退款数 | 自定义报表 |
| `returnSaleNum` | 退货数 | 自定义报表 |
| `returnSaleNumRate` | 退货率 | 自定义报表 |
| `buyableReturnNum` / `unBuyableReturnNum` | 可售 / 不可售退货数 | 自定义报表 |
| `replaceSaleNum` | 换货数 | 自定义报表 |

---

## 十二、库存总量 / 构成

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `totalInventory` | 总库存 | 在线产品 |
| `fbaTotalInventory` | FBA 总库存 | 自定义报表 |
| `fbaSaleInventory` | FBA 可售库存 | 自定义报表 |
| `fbaInventory` | FBA 库存 | 自定义报表 |
| `quantity` | 可售库存 | 在线产品 |
| `reservedQty` | 预留库存 | 在线产品 |
| `warehousing` | 入库中 | 在线产品 |
| `reservedTransfer` / `reservedProcessing` / `reservedCustomerorders` | 预留调拨 / 处理中 / 客单占用 | 自定义报表 |
| `inboundWorking` | 在途（工作中） | 自定义报表 |
| `inboundShipped` | 在途（已发） | 自定义报表 |
| `inTransit` | 运输中 | 自定义报表 |
| `inboundReceiving` | 入库接收中 | 自定义报表、在线产品 |
| `unfulfillable` / `unsellable` | 不可售 / 残次 | 自定义报表、在线产品 |
| `research` | 研究中 | 自定义报表 |
| `available` | 可售 | **本次实测未捕获** |

---

## 十三、本地仓 / 海外仓

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `localAvailableQty` | 本地可售量 | 自定义报表 |
| `overseaAvailableQty` | 海外可售量 | 自定义报表 |
| `domesticStockAvailable` / `overseaStockAvailable` | 本地 / 海外可售 | 自定义报表 |
| `domesticStockAllNum` / `overseaStockAllNum` | 本地 / 海外总量 | 自定义报表 |
| `domesticSafeStock` / `overseaSafeStock` | 本地 / 海外安全库存 | 自定义报表 |
| `domesticStockWait` / `overseaStockWait` | 本地 / 海外待处理 | 自定义报表 |
| `domesticStockOccupy` / `overseaStockOccupy` | 本地 / 海外占用 | 自定义报表 |

---

## 十四、库龄

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `invAge0to30Days` | 库龄 0~30 天 | 自定义报表 |
| `invAge31to60Days` | 库龄 31~60 天 | 自定义报表 |
| `invAge61to90Days` | 库龄 61~90 天 | 自定义报表 |
| `invAge0to90Days` | 库龄 0~90 天 | 自定义报表 |
| `invAge91To180Days` | 库龄 91~180 天 | 自定义报表 |
| `invAge181To270Days` | 库龄 181~270 天 | 自定义报表 |
| `invAge271To365Days` | 库龄 271~365 天（长期仓储费风险） | 自定义报表 |
| `invAge365PlusDays` | 库龄 365+ 天（长期仓储费红线） | 自定义报表 |

---

## 十五、周转 / 补货

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `turnoverDaysMonth` | 周转天数（月） | 自定义报表 |
| `turnoverRateMonth` | 周转率（月） | 自定义报表 |
| `isrMonth` | ISR（月） | 自定义报表 |
| `turnoverDaysQuarter` / `turnoverRateQuarter` / `isrQuarter` | 季度周转指标 | 自定义报表 |
| `salesVelocityMonth` | 月销速率（日均销量） | 自定义报表 |
| `availableDays` | 可售天数 | 自定义报表 |
| `fbmAvailableDays` | FBM 可售天数 | 自定义报表、商品分析 |
| `suggestNumSum` | 补货建议量 | 自定义报表 |

> 周转天数、ISR、补货建议量赛狐**已算好** —— 直接用官方口径，不自造算法。

---

## 十六、库存资金占用

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `fbaAvailableCost` | FBA 可售成本 | 自定义报表 |
| `fbaInboundWorkingCost` / `fbaInboundShippedCost` | 在途成本 | 自定义报表 |
| `fbaReservedCost` | 预留成本 | 自定义报表 |
| `fbaUnfulfillableCost` | 不可售成本 | 自定义报表 |
| `domesticStockAvailableCost` / `overseaStockAvailableCost` | 本地 / 海外可售成本 | 自定义报表 |
| `inventoryCosts` / `avgInventoryCost` / `avgTransportCost` | 库存 / 平均库存 / 平均运输成本 | **本次实测未捕获** |

---

## 十七、AWD

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `awdAvailable` | AWD 可售 | 自定义报表 |
| `awdOnhand` | AWD 在手 | 自定义报表 |
| `awdInbound` | AWD 在途 | 自定义报表 |
| `awdReserved` | AWD 预留 | 自定义报表 |

---

## 十八、赔偿 / 差异

> ⚠️ 以下字段**仅存在于 `get_custom_report_page_list`**，**不在 `get_profit_report_by_msku`**。

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `reimbursementAmount` | 赔偿金额 | 自定义报表 |
| `reversalReimbursementAmount` | 赔偿冲销 | 自定义报表 |
| `warehouseDamageAmount` | 仓库损坏赔偿 | 自定义报表 |
| `warehouseLostAmount` | 仓库丢失赔偿 | 自定义报表 |
| `missingFromInboundAmount` | 入库丢失赔偿 | 自定义报表 |
| `compensatedClawbackAmount` | 赔偿追回 | 自定义报表 |
| `incorrectFeesAmount` | 错误费用 | 自定义报表 |
| `compensationFee` | 赔偿费 | `get_profit_report_by_msku` |

---

## 十九、结算 / 延期

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `settlementStatus` | 结算状态 | 订单、`get_profit_report_by_order` |
| `deferredAmount` | 延期结算金额 | 自定义报表 |
| `deferredReason` | 延期原因 | `get_profit_report_by_order` |
| `collection` | 收款 | 利润报表 |
| `receiptAmount` / `realReceiptAmount` | 收款额 / 实收额 | `get_receipt_order_page_list` |
| `receiptStatus` | 收款状态 | `get_receipt_order_page_list` |
| `postedDate` | 入账日期 | `get_profit_report_by_order` |
| `estimatedSettlementTime` | 预计结算时间 | `get_profit_report_by_order` |

---

## 二十、Listing 属性

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `listingId` | Listing ID | 在线产品 |
| `onlineStatus` | 在售状态（`Active` / `inActive` / `delete`） | 在线产品 |
| `switchFulfillmentTo` | 配送方式（`AFN`=FBA / `MFN`=FBM） | 在线产品 |
| `standardPrice` | 标准价 | 在线产品 |
| `listingPrice` | 售价 | 自定义报表、在线产品 |
| `salePrices` | 销售价 | 在线产品 |
| `shippingPrice` | 运费价 | 自定义报表、在线产品 |
| `buyBoxWinner` | 是否赢得购物车 | 在线产品 |
| `isVariation` | 是否变体 | 在线产品 |
| `variationChildStr` | 变体子项 | 在线产品 |
| `itemDimensions` | 商品尺寸 | 在线产品 |
| `labels` | 标签（部门/状态/等级） | 在线产品、订单 |
| `productLabelList` | 产品标签分组 | 自定义报表、在线产品 |

---

## 二十一、库存管理子对象 inventoryManage

`get_online_product_page_list` 返回的 `inventoryManage{}`（**41 字段**），库存技能的金矿：

| 字段组 | 字段 | 用途 |
|---|---|---|
| **库龄分桶** | `invAge0to90Days`、`invAge91To180Days`、`invAge181To270Days`、`invAge271To365Days`、`invAge365PlusDays` | 滞销识别、长期仓储费预警 |
| 库存构成 | `available`、`reservedTransfer`、`reservedProcessing`、`reservedCustomerorders`、`inboundWorking`、`inboundShipped`、`inboundReceiving`、`unfulfillable`、`totalInventory` | 可售/在途/锁定拆解 |
| **资金占用** | `avgInventoryCost`、`avgTransportCost`、`inventoryCosts`、`purchaseCosts` | 库存资金占用、周转分析 |
| 商品信息 | `asin`、`parentAsin`、`sku`、`fnSku`、`commodityId`、`commodityName`、`commoditySku` | 关联本地商品 |
| 其它 | `productStatus`、`condition`、`isEfn`、`region`、`shareKey`、**`currency`（CNY）** | 币种陷阱 |

---

## 二十二、订单 / 广告任务 / 店铺（工具级）

### `get_order_page_list` —— 40 字段（含 PII）

`shopId`、`shopName`、`amazonOrderId`、`orderStatus`、`orderTotalCurrency`、`orderTotalAmount`、
`marketplaceId`、`orderType`、`comment`、`commentColor`、**`buyerName`（PII）**、**`buyerEmail`（PII）**、
`fulfillmentChannel`、`orderItemVoList`、`orderProfit`、`fbmCostOrigin`、`fbmCost`、`isReturnOrder`、
`isBusinessOrder`、`isReplacementOrder`、`replacedOrderId`、`promotionFlag`、`orderFlag`、`promotionIds`、
`isBuyerRequestedCancel`、`refundStatus`、`refundDate`、`purchaseDate`、`lastUpdateDate`、`paymentsDate`、
`isVineOrder`、`earliestShipDate`、`latestShipDate`、`latestDeliveryDate`、`earliestDeliveryDate`、
`isPrime`、`customOrder`、`lowCostStore`、`evaluation`、`settlementStatus`

### `get_sp_campaign_list` —— SP 广告活动（游标）

`shopId`、`state`（**仅用实测确认的 `enabled` / `paused` / `archived`**）、`portfolioId`、`campaignId`、
`name`、`targetingType`（`auto`/`manual`）、`budget`、**`servingStatus`**、`offAmazonBudgetControlStrategy`、
`campaignSite`、`strategy`、`adjustments`（如 `[{"predicate":"placementTop","percentage":50}]`）、
`startDate`、`endDate`、`creationDate`、`lastUpdatedDate`

> ⚠️ `servingStatus` 字段**真实存在**（实测值 `CAMPAIGN_PAUSED`），
> 但取值 **`BUDGET_CAPPED` 未经实测验证，不得作为诊断规则的判定条件**。

### `get_ad_download_task_page_list` —— 广告报表任务

`id`、`taskName`、`shopId[]`、`adTypeCode`（如 `sb`）、`reportTypeCode`（如 `adProductReport`）、
`reportState`（如「已生成」）、**`downloadUrl[]`**、`createTime`、`reportCycle`、`timeUnit`

> 💡 `downloadUrl` 指向 xlsx，是获取广告明细、绕开分页限制的重要通道。

### `get_shop_page_list` —— 店铺

`id`、`name`、`sellerId`、`region`、`marketplaceId`、`adStatus`、`status`

### `get_custom_report_stat_index_list` —— 30 节点指标树

| 一级指标 | mappedIndex | 下钻 |
|---|---|---|
| 销售额 | `sales_amount` | 订单数 → 待支付订单数 |
| 利润 | `profit` | 毛利率 → 成本金额 |
| 访客数 | `visitor_count` | 新客数 → 老客数 |
| 转化率 | `conversion_rate` | 加购率 → 收藏率 |
| 客单价 | `average_order_value` | 平均件数 → 商品件数 |
| 退款金额 | `refund_amount` | 退款订单数 → 退款率 |
| 广告花费 | `advertising_spend` | 广告销售额 → ROAS |
| 库存数量 | `inventory_quantity` | 可售库存 → 锁定库存 |
| 发货数量 | `shipped_quantity` | 配送时效 → 准时送达率 |
| 评价数量 | `review_count` | 好评率 → 差评数量 |

---

## 二十三、多平台广告字段

| 字段 | 含义 | 所属工具 |
|---|---|---|
| `clickCnt` / `imprCnt` | 点击数 / 曝光数 | Temu 报表 |
| `orderPayCnt` / `orderPayAmt` | 支付订单数 / 金额 | Temu 报表 |
| `adSpend` | 广告花费 | Temu 报表 |
| `cartCnt` | 加购数 | Temu 报表 |
| `goodsNum` | 商品数 | Temu 报表 |
| `netRoas` / `netAcos` | 净 ROAS / 净 ACOS | Temu 报表 |
| `grossRevenue` | 总收入 | TikTok GMV Max |
| `liveViews` | 直播观看数 | TikTok GMV Max |
| `costPerOrder` | 单订单成本 | TikTok GMV Max |

---

## 二十四、数据源速查表

| 想拿什么 | 用哪个工具 | 关键字段 |
|---|---|---|
| 全指标（一行一 MSKU） | `get_custom_report_page_list` | 489 字段 |
| 财务深度拆解 | `get_profit_report_by_msku` | 171 字段，标准化分页 `rows` |
| 店铺级汇总 | `get_profit_report_by_shop` | 标准化分页 `rows` |
| 订单级结算 | `get_profit_report_by_order` | 标准化分页 `rows`，74 字段 |
| Listing 属性 + FBA 库存 | `get_online_product_page_list` | 79 + `inventoryManage` 41 |
| 商品多维分析 | `get_product_analyze_list` / `_detail` | 36 字段 + `fieldsMap` / `charData` |
| 跨平台销量 | `get_multiplatform_sale_stats` | `productSaleDayOpenVo` |
| 订单明细 | `get_order_page_list` / `_detail` | 40 字段 / 60+ 字段 |
| SP 广告活动 | `get_sp_campaign_list` | 标准化分页 `rows` + 游标 |
| 广告报表明细 | `create_ad_download_task` → `downloadUrl` | xlsx |
| 自定义指标目录 | `get_custom_report_stat_index_list` | 30 节点指标树 |
| 已有自定义指标 | `get_custom_report_index_page_list` | `rows` |

---

## 二十五、币种陷阱

| 位置 | 币种 | 说明 |
|---|---|---|
| `get_online_product_page_list.currency` | **USD** | 售价口径 |
| `get_online_product_page_list.inventoryManage.currency` | **CNY** | **成本口径** |
| `get_custom_report_page_list.currency` | USD | 报表币种，可配置 |
| `get_profit_report_by_*` 的 `currency` 参数 | 可选 | 可按指定币种折算 |
| `rate` / `currencyList` | 汇率 | 出现在 `get_profit_report_by_msku` |

> **跨币种是财务类技能最常见的错误来源。** 售价（USD）与库存成本（CNY）直接相减会得到荒谬结果，
> 必须显式做汇率转换或统一到同一币种。
>
> 完整口径规则见 `METRIC_FORMULAS.md`。
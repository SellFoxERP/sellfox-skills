# 字段与数据源索引（field-sources.md）

> **v1.7.0**：本技能不再调用 `get_custom_report_page_list`（宽表字段分析由
> `custom-report-analysis` 技能承接）。下表该工具行与宽表字段仅作归属备查；
> 本技能运行时只用 `get_online_product_page_list` 等在线产品字段自算。

字段名一律以 `_shared/FIELD_DICTIONARY.md` 为准，**不得凭记忆书写**。本文件是本技能用到的字段的核对快照。

## 涉及工具

| 工具 | 角色 | 列表字段 | 关键参数 |
|---|---|---|---|
| `get_custom_report_page_list` | ~~已移交 custom-report-analysis~~（本技能不调用） | `data.rows` | `report_id`(必填)、`start_date`/`end_date`(必填, `yyyy-MM-dd`)、`shop_ids`(逗号分隔字符串)、`page_no`/`page_size` |
| `get_online_product_page_list` | 降级/补充 | `data.rows` | `shop_id_list`(数组)、`page_no`/`page_size`、`start_time`/`end_time` |
| `get_fba_shipment_page_list` | 在途货件 | `data.rows` | `shop_ids`(数组)、`create_time_start/end` 或 `update_time_start/end`（至少一组完整区间，≤360 天）、`status`(数组) |
| `get_awd_inventory_page_list` | AWD 库存 | `data.rows` | `page_no`(必填)、`page_size`(必填)、`shop_id` |

> 网关分页默认标准化，列表根字段统一是 `data.rows`（raw 模式才出现 `reportList`/`itemList` 等原始名）——
> 读取建议用共享库 `rows()` 自动识别，不硬编码历史键名。
>
> ⚠️ `get_fba_shipment_page_list` 的 `shop_ids` 契约为 **integer 数组**（`items: integer`），与店铺清单
> （`get_shop_page_list`）返回的字符串 ID（如 `"AD-10001"`）**不兼容**：按文档把店铺清单 ID 直接传入会被
> `INVALID_PARAMS` 拒（field_errors 指向 `shop_ids.0`，线上实测）。需从报表行取数字店铺 ID（如
> `shopId=10001`）传入；取不到数字 ID 时省略该参数（契约 default `[]`，即全店铺）。

## 字段 → 工具归属（已核对）

| 字段 | 出现于 | 实测样例 |
|---|---|---|
| `availableDays` | `get_custom_report_page_list` | 58 |
| `fbmAvailableDays` | `get_custom_report_page_list`、`get_product_analyze_list` | 42 |
| `suggestNumSum` | `get_custom_report_page_list` | 260 |
| `salesVelocityMonth` | `get_custom_report_page_list` | 12.2 |
| `sevenSaleNum` / `thirtySaleNum` | `get_custom_report_page_list` | 51 / 210 |
| `day7SaleNum` / `day14SaleNum` / `day30SaleNum` | `get_online_product_page_list` | 30 / 60 / 210 |
| `quantity` / `totalInventory` / `warehousing` / `unsellable` / `reservedQty` | `get_online_product_page_list` | 237 / 102 / 0 / … / 43 |
| `inboundWorking` / `inboundShipped` / `inboundReceiving` / `inTransit` | `get_custom_report_page_list`、`get_online_product_page_list`(inventoryManage) | 80 / 45 / 30 / 120 |
| `invAge0to30Days` … `invAge365PlusDays` | `get_custom_report_page_list` | 420…20 |
| `invAge0to90Days` … `invAge365PlusDays` | `get_online_product_page_list.inventoryManage` | — |
| `turnoverDaysMonth` / `turnoverRateMonth` / `isrMonth` | `get_custom_report_page_list` | 72 / 0.42 / 1.8 |
| `localAvailableQty` / `overseaAvailableQty` | `get_custom_report_page_list` | 640 / 380 |
| `fbaInventory` / `fbaTotalInventory` | `get_custom_report_page_list` | 1245 / 1280 |
| `awdAvailable` | `get_custom_report_page_list` | 120 |
| `domesticSafeStock` / `overseaSafeStock` | `get_custom_report_page_list` | 150 / 110 |
| `fbaAvailableCost` / `overseaStockAvailableCost` / `domesticStockAvailableCost` | `get_custom_report_page_list` | 8600 / 1800 / 2400 |
| `inventoryManage{}`（41 字段，含库龄/构成/资金占用，`currency`=CNY） | `get_online_product_page_list` | — |
| `msku` / `sku` / `spu` | `get_custom_report_page_list` | `["MSKU-DEMO-001"]`（**列表**） |
| `asin` / `parentAsin` / `fnsku` / `shopId` / `marketplaceId` / `title` | `get_custom_report_page_list`、`get_online_product_page_list` | `[{"key": "..."}]`（**列表**） |

> ✅ `threeSaleNum` 是真实字段（近 3 天窗口，属宽表，已移交 custom-report-analysis）。本技能基线以在线产品 `day30SaleNum` /
> `thirtySaleNum` / `sevenSaleNum`（降级源用 `day30SaleNum` / `day7SaleNum`）。
>
> ⚠️ **降级源 `get_online_product_page_list` 不返回** `availableDays` / `fbmAvailableDays` /
> `suggestNumSum` / `salesVelocityMonth` / `fbaInventory` / `localAvailableQty` / `overseaAvailableQty`
> —— 这些均属自定义报表。降级时只能用该工具自身的 `day30SaleNum`、`quantity`、
> `inventoryManage.*` 自算，且必须显式标注口径降级；字段缺的行标「需人工核查」。

## 币种

| 位置 | 币种 |
|---|---|
| `get_online_product_page_list.currency`（顶层，售价口径） | USD |
| `get_online_product_page_list.inventoryManage.currency`（成本口径） | CNY |
| `get_custom_report_page_list.currency`（可配置，默认 USD） | USD |
| 成本类字段（`fbaAvailableCost` 等）的币种 | **字段字典未明确** → 本技能按配置假设 CNY 并标注「⚠️ 需与财务核对」 |

跨币种**绝不直接相减**；确需合并时按 `config/inventory.yaml → currency.fx_rate` 折算并标注汇率来源与日期。

## 需人工核查清单（MCP 拿不到）

| 项 | 原因 | 输出标注 |
|---|---|---|
| 采购下单 | 赛狐 MCP 无采购接口 | 本技能只出建议 |
| 安全库存阈值修改 | 只能读 `domesticSafeStock`/`overseaSafeStock` | 只读 |
| 长期仓储费金额 | 无费率来源 | 未配置费率 → 「需人工核查」 |
| 每 SKU 供应商 / 运输方式 | MCP 不返回 | 需在 `config/inventory.yaml` 维护映射 |
| 采购负责人 | 无对应字段 | 建议单中「需人工指派」 |
| 到货周期（未命中配置时） | 业务参数，需使用者提供 | 「周期来源」列标「默认值⚠️」 |
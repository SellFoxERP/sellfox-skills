# 判定规则与到货周期（replenishment-rules.md）

本文件是计算层（`scripts/replenishment.py`）的口径说明。SKILL.md 的 Step 3 是摘要。

## 目录

- [1. 输入字段](#1-输入字段)
- [2. 日均销量 v](#2-日均销量-v)
- [3. 可售天数 d](#3-可售天数-d)
- [4. 在途量 inb](#4-在途量-inb)
- [5. 到货周期 L：三级解析 + 交互补全 + 回写](#5-到货周期-l三级解析--交互补全--回写)
- [6. 断货判定：P0 / P1 / P2 / 健康](#6-断货判定p0--p1--p2--健康)
- [7. 滞销判定与清货](#7-滞销判定与清货)
- [8. 双向矩阵](#8-双向矩阵)
- [9. 示例](#9-示例)

---

## 1. 输入字段

全部来自 `get_custom_report_page_list`（主源），降级时取自 `get_online_product_page_list.inventoryManage`。
字段名以 `_shared/FIELD_DICTIONARY.md` 为准。

| 用途 | 字段 | 实测样例 |
|---|---|---|
| 可售天数 | `availableDays`、`fbmAvailableDays` | 58 / 42 |
| 补货建议量 | `suggestNumSum` | 260 |
| 动销/销量窗 | `salesVelocityMonth`、`thirtySaleNum`、`sevenSaleNum` | 12.2 / 210 / 51 |
| 在途 | `inboundWorking`、`inboundShipped`、`inboundReceiving`、`inTransit` | 80 / 45 / 30 / 120 |
| 库龄分桶 | `invAge0to30Days` … `invAge365PlusDays` | 420…20 |
| 周转 | `turnoverDaysMonth`、`turnoverRateMonth`、`isrMonth` | 72 / 0.42 / 1.8 |
| 多仓 | `localAvailableQty`、`overseaAvailableQty`、`fbaInventory`、`awdAvailable` | 640 / 380 / 1245 / 120 |
| 安全库存（只读） | `domesticSafeStock`、`overseaSafeStock` | 150 / 110 |
| 资金占用（成本口径） | `fbaAvailableCost`、`overseaStockAvailableCost`、`domesticStockAvailableCost` | 8600 / 1800 / 2400 |

> ✅ `threeSaleNum` **是真实字段**（近 3 天窗口）。基线优先 `salesVelocityMonth` /
> `thirtySaleNum` / `sevenSaleNum`。

## 2. 日均销量 v

按可信度从高到低退化，**并记录来源**：

```
v = salesVelocityMonth / 30     （赛狐现成字段，首选）
    否则 thirtySaleNum / 30
    否则 sevenSaleNum / 7
    全缺 → v = None，标注「⚠️ 日均销量无数据，需人工核查」，缺口无法计算
```

不为算基线另发一次 MCP 调用 —— 直接用报表现成窗口字段，把调用次数降到最低。

## 3. 可售天数 d

```
d = 赛狐 availableDays           （官方口径，首选）
    否则 fbmAvailableDays
    降级源自算：可用库存 / v        → 来源标「自算(降级口径)」
      可用库存取在线产品真实字段：inventoryManage.available → 顶层 quantity → totalInventory
      （**不可**改用宽表的 fbaInventory/localAvailableQty，降级源不返回这些字段）
    全缺 → d = None → 判定「未知」，标注「需人工核查」
```

**不自造周转天数/可售天数算法**（除降级兜底外）—— 赛狐已算好，重算会与业务方对不上账。
降级源无 `availableDays`，其"自算"是**显式标注的口径降级**，不是替代官方口径。

> 降级源（`get_online_product_page_list`）字段名与宽表不同：日均销量用 `day30SaleNum/30`
> （退化 `day7SaleNum/7`），**没有** `salesVelocityMonth` / `suggestNumSum`。详见 `workflow.md §2`。

## 4. 在途量 inb

```
inb = inboundWorking + inboundShipped + inboundReceiving + inTransit
```

**四个字段全部缺失 == 无数据（None），不是 0。** 此时无法判断"在途能否救急"，判定为「未知」并标注
「⚠️ 在途无数据，需人工核查」，绝不按 0 处理（会把"有货在路上"误判成 P0）。

## 5. 到货周期 L：三级解析 + 交互补全 + 回写

配置在 `_shared/config/lead_time.yaml`（由使用者维护）。**解析优先级**：

```
by_sku  >  by_supplier  >  by_shipping_method  >  default
```

- 旺季系数（`seasonal_adjustment`，如 `"11-01~12-25": 1.3`）在基础值上**相乘**，可叠加多区间。
- 供应商 / 运输方式映射 MCP 拿不到，需在 `config/inventory.yaml → sku_supplier_map` /
  `sku_shipping_method_map` 维护。
- 四级都没命中 → 落到 `default`（默认 45 天）。**用到 default 必须在输出标注「默认值⚠️」**。

### 交互流程（渐进式记忆）

```
① 读 lead_time.yaml
② 对每个 MSKU 按优先级解析
③ IF 有 MSKU 落到 default（未命中配置）：
     → 收集成一张表，【一次性批量询问】，绝不逐个打断
     → "以下 12 个 MSKU 没有到货周期记录，请填写（可回车跳过用默认 45 天）"
④ 用本次回答继续计算
⑤ 询问是否【回写到 lead_time.yaml】   → 同意则持久化，下次自动命中
```

四条原则：**批量询问不逐个打断**、**渐进式记忆**、**输出标注来源**、**默认值必须显式标注**。
实现走 `shared/scripts/config_loader.py` 的 `resolve_lead_time()` 与 `ensure_params()`；
本技能用 `build_lead_time_specs()` 生成询问规格。

### 在补货建议单中的呈现

| MSKU | 可售天数 | 到货周期 | 周期来源 | 在途 | 缺口 | 建议量 | 紧急度 |
|---|---|---|---|---|---|---|---|
| A001 | 12 | 45 | 配置(供应商) | 0 | — | — | 🔴P0 |
| A002 | 25 | 45 | 默认值⚠️ | 100 | 0 | 260 | 🟡P2 |
| A003 | 20 | 12 | 本次填写 | 0 | 96 | 96 | 🔴P1 |

> **「周期来源」这一列很关键**：使用者一眼看出哪些行建立在不确定的默认值上。

## 6. 断货判定：P0 / P1 / P2 / 健康

阈值读 `_shared/config/thresholds.yaml → inventory`（`available_days_min` 默认 30、
`available_days_critical` 默认 15）。**绝不硬编码。**

```
IF d < L AND inb == 0            → P0 来不及：空运 / 海外仓调拨 / 暂停广告
ELIF d < L AND inb > 0           → P1 有缺口
  缺口 = v * (L - d) - inb
  建议量 = max(缺口, suggestNumSum)   （suggestNumSum 缺失则用缺口并标注）
  IF 建议量 <= 0（在途已覆盖）      → 判「无缺口」：缺口、建议量 clamp 到 0，
                                      紧急度降级为 P2（d < warn_days）或「健康」，
                                      绝不输出负数补货量，也绝不继续标 P1
ELIF d < warn_days               → P2 预警：按 lead_time 提前备货
ELSE                             → 健康
```

额外：`d < available_days_critical` 且 `inb == 0` 直接升 P0（更早触发）。
`d` 或 `inb` 无数据 → 判「未知」，输出「⚠️ 需人工核查」，不硬套 P0/P1。
**补货建议量恒 ≥ 0** —— 出现负数说明该行其实无缺口，应降级而非把负数量写进补货单误导采购。

## 7. 滞销判定与清货

```
低动销  = v < quadrant.high_velocity_min
库龄过热 = invAge271To365Days 占比 > inv_age_271_365_pct_max
        或 invAge365PlusDays 占比 > inv_age_365_plus_pct_max
滞销    = 低动销 AND 库龄过热
       → 清货建议：降价 / 促销 / 移除，规避长期仓储费
```

库龄占比分母：默认按各分桶求和（`dead_stock.age_denominator: bucket_sum`），
可改为 `fba_total_inventory`。长期仓储费金额**无配置费率时标「需人工核查」，不编造**。

## 8. 双向矩阵

```
              高销量
   ① 紧急补货 │ ② 健康
  ───────────┼───────────→ 库存水位
   ③ 滞销清理 │ ④ 低效观察
              低销量
```

| 象限 | 条件 |
|---|---|
| ① 紧急补货 | 高销量 且 紧急度 ∈ {P0,P1,P2} |
| ② 健康 | 高销量 且 不紧急 |
| ③ 滞销清理 | 低销量 且 滞销 |
| ④ 低效观察 | 低销量 且 非滞销 |

## 9. 示例

**示例 1（P0）：** `availableDays=12`，`lead_time=45`（配置命中供应商），在途四项全 0。
→ 12 < 45 且 inb=0 → **P0**，动作：空运/海外仓调拨 + 暂停广告。

**示例 2（P1）：** `availableDays=8`，`lead_time=20`（配置命中），在途=0，`salesVelocityMonth=36`（v=1.2）。
→ 8 < 20 且在途=0？不，这里在途为 0 → 应为 P0。改：在途=5。
→ 8 < 20 且 inb=5>0 → **P1**；缺口 = 1.2*(20-8) - 5 = 9.4；`suggestNumSum=96`
→ 建议量 = max(9.4, 96) = **96**。

**示例 3（未知）：** 在途四项全缺 → 判「未知」，输出「⚠️ 在途无数据，需人工核查」，不判 P0。

**示例 4（滞销）：** v=1（< 10），`invAge271To365Days` 占比 18%（> 10% 阈值）→ **滞销**，
动作：清货；费率未配置 → 长期仓储费标「需人工核查」。
# 报告模板（report-template.md）

> 何时读：**渲染输出前必读**。定义问题清单结构、字段来源标注规则、披露区块。

## 目录

- [1. 报告结构](#1-报告结构)
- [2. 顶部披露区块（强制）](#2-顶部披露区块强制)
- [3. 问题清单模板](#3-问题清单模板)
- [4. 字段来源标注规则](#4-字段来源标注规则)
- [5. 尾部诚实边界（强制）](#5-尾部诚实边界强制)

## 1. 报告结构

```
# Listing 健康体检报告（<日期>）
## 0. 数据说明                ← 覆盖范围 / 口径降级 / 演示水印 / 时区 / 币种 / 截断
## 1. 红线清单（P0/P1）
## 2. 问题清单（按 P0→P3 排序）
## 3. 需人工核查清单
## 4. 本技能不做什么（诚实边界）
```

## 2. 顶部披露区块（强制）

逐项写明，缺一不可：

```
> 覆盖范围：N 个店铺 / 扫描状态 active,inActive,delete /
> 时间窗口：近 7 天与近 30 天（口径：<站点/时区说明>）
> 数据源：get_online_product_page_list（主）；get_product_analyze_list（可选）
>         ⚠️ 口径降级：未接入自定义报表（由 custom-report-analysis 技能承接），sessions/CVR/buyBoxPercent/类目排名/差评数不可用
> 截断披露：本次仅覆盖 Top N，其余未纳入（若触顶）｜ 未触发截断
> 演示环境：⚠️ 演示数据，非真实经营数据（命中指纹 <fingerprint>）｜ 未命中
> 币种：价格字段为 USD；库存成本子对象为 CNY（未直接相减）
```

## 3. 问题清单模板

按严重度排序，每条固定三段：**结论 → 证据（含字段来源）→ 建议**。

```
### [P0] 不可售 — B0MOCKA001（店铺：演示店铺001）
- 证据：onlineStatus = "inActive"
        字段来源：get_online_product_page_list.onlineStatus
- 建议：立即核查卖家后台账户健康/绩效通知（MCP 拿不到原因，需人工核查）

### [P1] 丢购物车 — B0MOCKA002
- 证据：buyBoxPercent = 78%（阈值 90%）｜ 口径降级：仅有 buyBoxWinner 布尔
        字段来源：get_online_product_page_list.buyBoxWinner（占比为宽表字段，custom-report-analysis 承接）
- 建议：检查跟卖/价格/库存；竞品价需人工核查

### [P2] BSR 恶化 — B0MOCKA003
- 证据：bsrRank 名次 141 → 320（+127%，阈值 30%）
        字段来源：get_online_product_page_list.bsrRank
- 建议：核查关键词与类目节点；类目名次需人工核查
```

固定排序：**P0（不可售/删除）→ P1（丢购物车/无流量/价格）→ P2（评分/BSR/CVR）→ P3（标签）**。同级内按影响面（有销量优先、金额/名次幅度优先）。

## 4. 字段来源标注规则

**每个证据值后面必须标注字段所属工具**（归因表逐字段标注），因为字段名因工具而异、且部分字段只在宽表：

- `get_online_product_page_list` → 标 `（online）`
- `get_custom_report_page_list` → 已移交 custom-report-analysis，本技能不产出该来源
- `get_product_analyze_list` → 标 `（analyze）`
- `get_product_by_label` → 标 `（label）`

归属以 `config/field-map.yaml` 为准，**不得凭记忆书写**。若字段实为「取不到」，写 `需人工核查` 而不是留空或 0。

## 5. 尾部诚实边界（强制）

```
---
本技能只诊断不修改，不会改动任何 Listing。
以下数据拿不到，需人工核查：竞品价格、类目平均 CVR、账户健康/政策合规、评论原文、抑制原因。
空数据标「无数据」，未写 0；降级口径已在上方标注。
```

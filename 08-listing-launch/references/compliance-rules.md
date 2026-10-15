# 合规检查规则（写前咨询门，永不写入）

> 当需要执行/解释合规检查、或维护禁词表时读本文件。
> 实现：`scripts/check_compliance.py`；词表：`config/compliance.yaml`。

---

## 一、定位：pre-write advisory gate, never writes

参考 `listing-compliance` 的只读检查思路：本环节**只输出风险提示，绝不做任何修改**。
它不调用任何写工具，不改文案，不改 Listing。把合规做成一道「写前门」，而不是自动修复器。

> 为什么：合规结论涉及法律责任，自动改写风险极高。技能只负责提醒，
> 判断与决策必须由人来做。

---

## 二、检查项

| 检查 | 数据来源 | 命中后的动作 |
|---|---|---|
| **禁词扫描** | `compliance.yaml → banned_words` | 列出命中词及所在字段 |
| **商标/专利** | 本技能**无数据库** | 固定提示「需人工核查」，绝不下结论 |
| **品类审核** | `compliance.yaml → category_approval_hints` | 提示确认类目资质要求 |
| **AI 素材披露** | `compliance.yaml → ai_label_required` | 提示部分平台要求披露 AI 生成内容 |
| **文案超限** | `config/listing_spec.yaml` | 标 OK / 超限 / 需人工核查 |

---

## 三、禁词表（示例起点，需按平台政策维护）

`config/compliance.yaml` 已内置一批**示例**高风险词（绝对化/承诺性表述）：

```
best seller / best-seller / bestselling / #1 / number one / top rated
guaranteed / 100% safe / cure / fda approved / epa approved / eco-friendly / free shipping
```

> ⚠️ 这是**起点**，不是权威清单。请按目标站点与类目的最新官方政策维护，
> 并自行补充品牌/专利相关关注词。

---

## 四、AI 生成素材的标注

生图/生视频产出的素材属于合成内容。命中 `ai_label_required: true` 时，
输出中固定加一条披露提示（见 `check_compliance.check_compliance` 的 `ai_disclosure`）。

---

## 五、边界（必须如实呈现）

- 本检查**不构成法律意见**，也不能替代平台官方审核；
- 商标/专利/资质：本技能无相关数据源，**一律需人工核查**；
- 禁词表命中 ≠ 平台一定拒审；未命中 ≠ 平台一定通过。

免责声明来自 `config/compliance.yaml → disclaimer`，会写入上架包区块 5。
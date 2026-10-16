# 工具覆盖矩阵（22 个技能依赖工具 × 评测用例）

> 目标零缺口：每个工具至少有一个「主数据源用例」真实调用它。
> 用例编号：`技能号-id`（各技能 evals/evals.json）或 `X-id`（tests/evals/cross-skill-queries.json）。

| # | 工具 | 域 | 读写 | 主用例 | 辅助用例 |
|---|---|---|---|---|---|
| 1 | `get_shop_page_list` | shops | 读 | 02-7（分批体检） | 01-1、X-3、X-18 |
| 2 | `get_profit_report_by_shop` | finance | 读 | 01-6（发货口径） | 04-6、04-8、05-6、X-1、X-13 |
| 3 | `get_profit_report_by_msku` | finance | 读 | 04-5（Top50 截断披露） | 05-7、05-8、X-5 |
| 4 | `get_custom_report_page_list` | reports | 读 | 01-9（契约失配注入） | 02-7、03-6、05-5、09-5、X-14 |
| 5 | `get_order_page_list` | orders | 读 | 01-8（PII 脱敏） | — |
| 6 | `get_sp_campaign_list` | advertising | 读 | 06-1（游标分页） | X-5、X-9 |
| 7 | `get_sp_keyword_list` | advertising | 读 | 06-6（优质词提案） | X-5 |
| 8 | `get_sp_ad_product_list` | advertising | 读 | 06-1 | — |
| 9 | `get_sp_product_targeting_list` | advertising | 读 | 06-6（投放扩量） | — |
| 10 | `get_multiplatform_sale_stats` | analytics | 读 | 01-5（平台销量对比） | X-12 |
| 11 | `get_online_product_page_list` | orders | 读 | 03-7（契约失配注入） | 02-4、08-5、09-5、X-7 |
| 12 | `get_product_analyze_list` | products | 读 | 03-5（BSR 恶化） | 08-6 |
| 13 | `get_product_by_label` | products | 读 | 03-4（标签体检） | — |
| 14 | `get_fba_shipment_page_list` | logistics | 读 | 02-5（在途救急） | X-4、X-18 |
| 15 | `get_awd_inventory_page_list` | logistics | 读 | 02-6（空数据不写 0） | 02-5 |
| 16 | `get_amazon_reimbursement_report` | finance | 读 | 05-5（赔偿交叉核对） | — |
| 17 | `create_ad_download_task` | advertising | 写 | 06-5（任务链，发起前确认） | — |
| 18 | `get_ad_download_task_page_list` | advertising | 读 | 06-5（轮询进度） | — |
| 19 | `get_custom_report_stat_index_list` | reports | 读 | 07-4（指标树两视角） | 07-1 |
| 20 | `get_custom_report_index_page_list` | reports | 读 | 07-5（更新前查询） | 07-2 |
| 21 | `add_custom_report_index` | reports | 写 | 07-2（批准后写入） | — |
| 22 | `update_custom_report_index` | reports | 写 | 07-5（快照旧公式） | 07-7 |

广告写操作（技能 06 专用，提案/审批后 agent 执行）：

| 工具 | 主用例 |
|---|---|
| `edit_sp_campaign` | 06-3（批准执行）、06-9（写契约失配）、X-9（幅度闸门） |
| `edit_sp_targeting` | 06-6（提案 dry-run）、06-9 |
| `edit_sp_ad_group` / `edit_sp_ad_product` | 06-1 诊断覆盖（提案构造路径） |

新平台工具（cross-skill 覆盖，供后续扩展）：

| 工具 | 用例 |
|---|---|
| `get_walmart_sp_keyword_report_page_list` | X-16 |
| `get_temu_full_managed_transaction_overview`（及分项系列） | X-17 |
| `get_tiktok_profit_report_by_shop` | X-13 |

**缺口结论：22/22 零缺口**；场景注入类（契约失配/INVALID_PARAMS/累计闸/read-back 失败）分布：
01-9/10、02-8、03-7、04-8、05-8、06-7/9/10、07-7、08-6、09-7。

## 12 技能时代的补充覆盖（2026-10-16，技能 10/11/12 新增评测）

技能 10（通用业务）/11（退款损失）/12（新品复盘）的用例不绑定固定取数计划——工具按
实时 help/search 发现（SKILL.md「索引未覆盖先 help」原则），因此覆盖矩阵按「线上评测
实际调用过的工具」记录（来源 out/eval-live-2026-10-16/10~12-*.md）：

| 技能 | 线上评测实际调用的工具 | 用例 |
|---|---|---|
| 10-sellfox-business | `get_shop_page_list`、`get_multiplatform_sale_stats`、`get_order_page_list`、`get_product_analyze_list`、`get_fba_shipment_page_list`、`get_awd_inventory_page_list`、`get_profit_report_by_shop`、`get_sp_campaign_list`、`get_custom_report_index_page_list`、`get_custom_report_stat_index_list`、help（no_match 场景） | 10-1~10-14（14 例） |
| 11-returns-loss | `get_profit_report_by_msku`、`get_shop_page_list`（+ 复用 10 的 process/topic 组件） | 11-1~11-7（7 例） |
| 12-new-product-review | `get_online_product_page_list`、`get_product_analyze_list`、`get_profit_report_by_msku` | 12-1~12-7（7 例） |

新记录的数据源事实（线上实测，详见线上评测 SUMMARY §四）：`stat_index_page_list` 形态
工具不存在（正确为 `stat_index_list`）；三域商品/店铺键空间互不一致，跨域 JOIN 线上不可达。

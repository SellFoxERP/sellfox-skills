# 广告（advertising）

用于广告结构、绩效和已授权调整。调用遵循 [MCP 流程](mcp-workflow.md)，先区分平台、广告类型和读写目的。

## 工具导航

| 需求 | 已知入口或发现线索 |
|---|---|
| Amazon SP 结构 | `get_sp_campaign_list`、`get_sp_ad_group_list`、`get_sp_keyword_list`、`get_sp_ad_product_list`、`get_sp_product_targeting_list` |
| Amazon SB 结构 | `get_sb_campaign_list`、`get_sb_ad_group_list`、`get_sb_keyword_list`、`get_sb_ad_product_list` |
| Amazon SP/SB/SD 绩效下载 | `create_ad_download_task`、`get_ad_download_task_page_list` |
| Amazon 调整 | `edit_sp_campaign`、`edit_sp_targeting`、`edit_sb_campaign` 等；用具体变更问句确认支持的对象和字段 |
| 其他平台 | `get_mercado_campaign_list`、`get_shopee_campaign_report_page_list`、`get_temu_shop_report_page_list`、`get_tiktok_gmv_max_campaign_list`、`get_walmart_sp_campaign_report_page_list` 等，按实际平台发现 |

问句示例：“查询 Amazon SP 活动和关键词，分析最近七天的花费与成交，只给建议。”仅传查询所需真实店铺/账户/活动 ID；不同平台账户、商店与活动层级不通用。SP、SB、SD 不可互换。

## 结构与绩效分开

活动、竞价、状态、预算属于结构，不能据此计算 ACOS/ROAS 或判断浪费。先找匹配店铺、类型、日期的已有绩效报告；需要创建任务时按用户授权和当前契约执行，并检查进度、失败原因与结果文件是否就绪。

下载任务列表不是绩效数据。下载文件须匹配本次任务 ID、店铺、报告类型和窗口，使用服务实际返回且可用的地址，不把示例地址当数据源。创建成功不能宣称已拿到绩效。客户端不能读取文件时说明限制，可请用户提供导出数据。

按当前契约续页，注意游标有效期；过期重新查询并去重，不把重取页追加成新增活动。用同一归因窗口和层级关联绩效；商品级费用不能分摊伪装为活动级实测。ACOS 分母无销售额时标不可计算，不能当 0；没有绩效时输出结构快照和数据缺口。

## 变更与核验

先展示具体对象的当前值、拟调整值、金额/币种、原因与影响范围。用户明确批准后，取得当前写契约并执行批准项；竞价、预算、状态、否定词/否定商品应分别匹配工具。不套用固定百分比作为所有商家的阈值。

写入后按对应平台和对象读取核验。partial/unknown 时保留逐项结果并核实，不整批重放。未获取绩效或样本不足时不自动提出确定性的关停结论。

## 数据处理脚本

结构表按状态等分组 count；绩效另用同粒度输入，花费用 sum/money，ACOS 用花费/归因销售额的 ratio/percent，ROAS 用归因销售额/花费的 ratio/ratio，均 currency_sensitive=true。CTR/CVR 分母及归因定义按契约，不能用结构表生成绩效。 按 [Python 数据处理](data-processing.md) 生成本次计划，运行 `scripts/process_data.py`，保留逐页来源和警告。

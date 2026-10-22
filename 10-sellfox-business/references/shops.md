# 店铺与基础信息（shops）

用于确认用户能访问哪些店铺、店铺名称对应的 ID 与返回的站点线索（region/marketplaceId），为后续查询提供可靠范围。调用遵循 [MCP 流程](mcp-workflow.md)。

## 发现与执行

- 问句示例：“查询我有权限的店铺列表，保留店铺 ID、名称、region 和 marketplaceId。”
- 已知入口：`get_shop_page_list`。缺当前契约时 sf_describe，再按契约分页。
- 查询行政区划可发现 `get_province_list`；它不是店铺或仓库清单，不能用于推断业务权限。
- 用返回字段匹配用户指定的店铺。相似名称、多站点或多个平台时给出候选，让用户确定范围；“全部店铺”仅指当前账号权限内已覆盖的店铺。
- 保留店铺 ID 与返回的 region/marketplaceId，复用于本次任务，避免每个域重复发现。

## 结果与边界

输出店铺表及覆盖状态。输出契约不直接返回平台字段（实测 output_fields 仅 id/name/sellerId/region/marketplaceId/adStatus/status 一类）：平台/站点只能按 region/marketplaceId 推断，且必须标注为推断值；币种未返回时标未知，不从名字推定。后续请求需要币种、时区或授权时，不能把“能查到店铺”当作已经确认这些信息。

若列表为空，说明当前查询没有返回店铺；若权限失败，说明连接可达但业务权限不足，不能称为账号没有店铺。不要输出凭证或不相关的账户信息。

## 数据处理脚本

店铺清单通常直接展示。需要数量分布时，按 region/marketplaceId 分组（作为平台/站点口径时同样标注为推断），用 count 统计店铺记录；去重键使用当前返回的真实店铺 ID。没有日期字段的清单不设置 period。 按 [Python 数据处理](data-processing.md) 生成本次计划，运行 `scripts/process_data.py`，保留逐页来源和警告。

# 销量下降专项排查

先用销售统计/产品分析确定同店铺、同商品、等长窗口的下降事实。get_product_analyze_list 的 fields 以当前契约为准，可按需读取销量、conversionRate、广告曝光/点击/花费；不得把库存快照当历史断货证据。

排查顺序按证据选择：在线状态异常→库存是否不足→流量/点击变化→转化变化→广告结构或成本变化。缺流量时不能认定流量下降，缺历史状态时不能认定不可售导致此前掉单。自然流量与广告归因窗口不同，不能简单相减补造自然销量。

完整包的 sellfox-business/scripts/scenarios.py（源码目录 10-sellfox-business）支持 sales_change：输入 mode、source_note、current_sales、baseline_sales、current_days、baseline_days，计算等长窗口差值与增长率；零基期增长率未知。运行 python <组件目录>/scripts/scenarios.py --input <输入.json> --output <新输出.json>。

交付商品下降贡献、已证实异常、待验证假设、缺失证据、建议排查动作。观察到同时变化不能写成因果证明。新近上市商品优先转新品上市复盘，按上市年龄对比。没有当前/基期完整数据时只报告已取得样本。

# 计算计划

从 [计划示例](plan-example.json) 开始，根据本次真实契约修改路径、字段、日期和分组，不把示例日期或口径作为商家默认值。计划由助手生成，文件路径相对计划所在目录。

输入是完整业务信封，或 {"request":原请求,"response":业务信封}。datasets 可含 current/baseline，添加 compare: {"current":"current","baseline":"baseline"} 计算两期差值与变化率；百分比差值用百分点。各 dataset 可用 request_match 核对原始请求参数。失败页保留诊断，缺失数值不当零，去重冲突排除，币种分组；不平均各行百分比。

商品键必须是当前返回里已验证的稳定标量，示例 id 的含义需先核对。不能为了运行脚本取 mskuList.0 或将聚合行强行拆分。标识无法核实时只输出原始样本和缺口。所有输出标记全量覆盖未认证。

脚本生成 result.json、report.html（主报告，附同名 .md 留档）、plan.json；拒绝覆盖已有目录。退出 0 只表示处理完成（可能 empty/partial），2 表示所有数据集不可用，1 表示计划或文件错误。不将计算报告当作已验证的经营结论。新技能依赖完整包中的 sellfox-business/scripts/topic_review.py 和 process_data.py；不依赖第三方库。

## 用户要求展示返回样本时

仅在用户明确要求能力演示时，由助手设置 mode=sample 和 topic.sample_reason（授权依据、历史日期/范围差异）。保留原始请求、原始业务日期及金额，不将样本改成当前日期；报告前部强制展示样本警示。生产模式仍排除日期越界行。样本模式不绕过 failed/unknown、错误工具、缺失币种或去重冲突校验。

新品可设置 topic.max_age_days（正整数，来自本次新品定义），输出 within/outside/unknown；outside 不得归入新品表现榜。日期时间戳时区不明时保留原值、年龄未知，不能为了跑通而截断。

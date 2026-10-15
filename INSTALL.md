# 在 WorkBuddy 中批量安装赛狐技能

本指南安装全部 9 个技能，一份共享配置供它们复用。完整 ZIP 是**批量安装分发包**，不是声称 WorkBuddy“上传技能”入口支持一次导入多技能 ZIP。

## 1. 准备

- WorkBuddy 桌面客户端，能够使用自定义 MCP、技能和本地脚本。
- 商家自己的赛狐 MCP 地址与 `X-MCP-Key`，由赛狐提供；本仓库不附带任何凭证。
- Python 3.10 或更新版本。Mac/Linux 检查 `python3 --version`；Windows 检查 `py -3 --version`。若低于 3.10，先选择已安装的新版本或安装 Python。
- 核心计算仅依赖标准库。需要广告 xlsx 解析时安装 `openpyxl`；复杂 YAML 配置建议安装 PyYAML：`python3 -m pip install -r requirements-optional.txt`。

## 2. 配置 Sellfox-MCP

在 WorkBuddy 的连接器/MCP 设置中添加远程服务，名称建议为 `Sellfox-MCP`，协议选择 **Streamable HTTP**，填写赛狐提供的 HTTPS 地址，在请求头中添加 `X-MCP-Key`。

以下是 JSON 配置示例；URL 与 Key 必须替换。不同客户端的传输类型字段可能不同，以当前 WorkBuddy 界面或导出格式为准，不把其他客户端的 `http` 写法当成通用要求。

```json
{
  "mcpServers": {
    "Sellfox-MCP": {
      "type": "streamableHttp",
      "url": "https://YOUR-SELLFOX-HOST/mcp",
      "headers": { "X-MCP-Key": "YOUR-SELLFOX-MCP-KEY" },
      "disabled": false
    }
  }
}
```

保存并在当前任务启用连接器。如果看不到工具，新建任务或重新加载客户端。对 WorkBuddy 说：

> 检查 Sellfox-MCP 是否能连接，列出 help、search、action 工具。只查询店铺列表验证访问，不执行写操作，不展示 Key。

`help` 发现业务能力，`search` 读取已知能力的当前契约，`action` 执行业务查询/操作。已有完整契约时无需重复 search。

## 3. 一次安装全部技能

从 GitHub 下载完整仓库（Code → Download ZIP），或下载 Release 附件 `sellfox-skills-workbuddy.zip`，解压。

**可以直接把下面的话发给 WorkBuddy，并选择解压后的目录：**

> 请阅读这个目录的 INSTALL.md，确认 Python 至少 3.10，确认当前 WorkBuddy 使用的技能目录，然后运行 scripts/install.py 一次安装全部 9 个技能。不要逐个导入，不配置或输出我的 Key；若有同名安装，先说明升级会保留哪些配置。

也可以在解压目录运行：

```bash
# Mac / Linux
python3 scripts/install.py
# Windows
py -3 scripts/install.py
```

默认目标为用户主目录下 `.workbuddy/skills`。若客户端实际使用另一目录，明确指定：

```bash
python3 scripts/install.py --skills-dir "/实际的技能目录"
```

安装结果：

```text
<技能目录>/
├── daily-ops-report/ ... voc-insight/   # 9 个可发现技能，目录名与 name 一致
├── _sellfox_shared/                    # 一份共享配置和代码，不是技能
└── .sellfox-install.json               # 本安装器的管理标记
```

安装器不会改 MCP 设置，不访问网络、不发送消息；遇到同名目录默认停止。安装后在“已安装技能”中确认九个技能已启用；若未显示，确认目标目录并重新加载客户端。此路径支持由安装器临时目录测试验证，客户端发现与触发仍需在对应 WorkBuddy 版本验收。

## 4. 第一次使用与配置

先用日报或库存体检。示例：

> 生成昨天经营日报；先确认日期范围、店铺、币种和利润口径。输出本地报告，不修改广告或指标，不发送飞书。缺少报表或字段请说明影响。

所有共用配置放在 `<技能目录>/_sellfox_shared/config/`：

| 文件 | 何时填写 |
|---|---|
| `reports.yaml` | 宽表分析需要自定义报表 ID；由赛狐页面获取。日报及利润诊断目前使用 `daily_ops.id`，库存使用 `inventory.id` |
| `shops.yaml` | 指定店铺、负责人；不填写则按查询权限和用户范围取数 |
| `thresholds.yaml` | 按业务调整异常阈值；默认值是起始配置，不是行业标准 |
| `lead_time.yaml` | 补货到货周期、运输方式及旺季系数 |
| `targets.yaml` | 经营目标；没有实际目标时应留空，不使用示例数值 |

单技能配置在对应 `config/` 中。币种、日期、时区和利润口径需在取数前确认，并在报告中标注。没有 `report_id` 时部分指标不可用，不能把缺失值当作零。

报告通常输出到各技能的 `out/`；原始响应在 `out/raw/`。库存报告可显式指定输出文件；其他多段流水线按技能 workflow 指定 `out/` 文件。直接运行脚本前，需由 AI 按取数计划取得数据；脚本本身不连接赛狐。

## 5. 可选能力

- **广告优化**：先运行诊断、审阅提案。生产修改需要用户明确批准；本仓库离线测试不执行生产广告变更。
- **指标治理**：公式必须由商家确认；默认示例公式为空，不会自动创建指标。
- **上架素材**：文案可由 AI 根据已确认的商品事实补全；生图/视频需另选服务并确认成本。无完整成本时仅列成本缺口，不能给完整利润结论。
- **评论分析**：赛狐聚合指标用于定位问题；评论原文需外部源或商家提供的合法导出。没有原文只生成聚合定位报告。
- **飞书输出**：另配 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和目标权限，按需设置 `FEISHU_FOLDER_TOKEN`；审批通知可配置 `FEISHU_APPROVAL_WEBHOOK`。只有用户要求外发时才启用；测试未验证飞书真实投递。

## 6. 升级、迁移与卸载

升级前备份商家配置，下载新版完整包，在解压目录运行 `python3 scripts/install.py --update`，自定义目录需同时传 `--skills-dir`。升级保留现有 `config/`、`out/`、`logs/`，更新代码和文档；新增配置键需对照新版说明按需补充。

移动安装目录后重新运行安装器并指定新目标，以更新共享路径。卸载时在客户端移除九个技能；确认不再需要数据后再清理 `_sellfox_shared` 和管理标记。不要移除其他技能。

## 7. 排查问题

| 现象 | 处理 |
|---|---|
| 找不到技能 | 确认实际技能目录、有九个 SKILL.md、已启用；重载/新建任务 |
| 找不到 MCP 工具 | 检查当前任务是否启用连接器、协议与认证头；不要公开 Key |
| 找不到 `_shared` | 应批量安装，检查 `config/paths.yaml` 的共享路径；旧 `SELLFOX_SHARED_ROOT` 环境变量优先级更高 |
| 提示没有原始数据 | 让 AI 先完成 workflow 的取数步骤，再运行分析 |
| `unknown` / `partial` / 契约警告 | 保留原响应，披露缺口；写结果未知先核实，禁止直接重放 |
| 只有部分数据 | 按当前契约分页，披露覆盖数量；单页成功不等于全量完成 |
| 缺少 `openpyxl` | 安装可选依赖，或按技能支持的降级路径处理 |

官方入口说明：[技能](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)、[连接器](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector)。菜单可能随客户端版本调整。这里采用项目提供的批量安装器，不依赖官方未明确说明的多技能 ZIP 导入行为。

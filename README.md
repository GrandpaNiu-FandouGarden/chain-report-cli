# ChainReport CLI

可替换底层模型的产业链研究报告命令行工具。

## 定位

- 独立 CLI 工具。
- 不读取邮件。
- 不替代现有每周五产业链周报定时任务。
- 输出本地 Markdown 文件，并在同目录生成本地 SVG 图表。
- 报告正文采用确定性数据处理和业务规则生成：多品种 Wind 取数、18 周周均、环比、连续涨跌、风险/机会信号、剪刀差和 Wind 资讯，不依赖大模型编造正文。

## 首次配置

交互式配置 Wind Key、模型 provider、模型名、base_url 和模型 Key：

```bash
python3 -m chain_report.cli configure
```

非交互配置示例，适合定时任务部署：

```bash
python3 -m chain_report.cli configure \
  --provider openai-compatible \
  --model your-model-name \
  --base-url https://your-openai-compatible-endpoint/v1 \
  --wind-key YOUR_WIND_KEY \
  --llm-key YOUR_MODEL_KEY \
  --wind-node-bin /path/to/node \
  --wind-cache-dir .cache/wind \
  --wind-cache-ttl-seconds 3600 \
  --non-interactive
```

配置会保存到当前项目的 `.env`。`.env` 已在 `.gitignore` 中，不应提交到仓库。

查看当前配置，Key 会脱敏：

```bash
python3 -m chain_report.cli config-show
```

配置完成后，CLI 会询问是否立即生成报告。也可以显式指定：

```bash
python3 -m chain_report.cli configure --run-now
```

## 运行

配置完成后，直接生成报告：

```bash
python3 -m chain_report.cli run
```

默认报告输出：

```text
outputs/steel-weekly/YYYY-MM-DD/report.md
outputs/steel-weekly/YYYY-MM-DD/charts/*.svg
```

测试流程，不调用 Wind、不调用真实模型：

```bash
python3 -m chain_report.cli generate --sample-data --provider mock --non-interactive --no-save-keys
```

测试输出位置：

```text
outputs/steel-weekly/YYYY-MM-DD/report.md
```

## Key 和默认配置

CLI 会优先读取当前目录 `.env` 或环境变量：

- `CHAIN_REPORT_PROVIDER`
- `CHAIN_REPORT_MODEL`
- `CHAIN_REPORT_BASE_URL`
- `CHAIN_REPORT_NODE_BIN`
- `CHAIN_REPORT_WIND_MCP_DIR`
- `CHAIN_REPORT_WIND_CACHE_DIR`
- `CHAIN_REPORT_WIND_CACHE_TTL_SECONDS`
- `WIND_API_KEY`
- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `MINIMAX_CN_API_KEY`
- `MINIMAX_API_KEY`
- `OPENAI_COMPATIBLE_API_KEY`

如果不想保存输入的 Key：

```bash
python3 -m chain_report.cli generate --no-save-keys
```

## 当前支持的模型 provider

- `mock`：本地占位输出，用于验证流程
- `openai`
- `deepseek`
- `minimax-cn`
- `minimax`
- `openai-compatible`
- `ollama`

## Wind 数据缓存

CLI 默认启用单次运行内存缓存：同一次报告生成过程中，相同的 Wind query 只会调用一次 Wind MCP。

如需跨进程/跨运行复用 Wind 数据，可显式开启磁盘缓存：

```bash
python3 -m chain_report.cli generate \
  --non-interactive \
  --wind-cache-dir .cache/wind \
  --wind-cache-ttl-seconds 3600
```

注意：磁盘缓存默认不开启，避免“最新/最近三个月”这类查询误用过期数据。`--wind-cache-ttl-seconds 0` 表示关闭磁盘缓存。

如需关闭单次运行内存缓存：

```bash
python3 -m chain_report.cli generate --non-interactive --no-wind-memory-cache
```

## 当前报告口径

本地版保留 OpenClaw 定时任务中影响报告质量的核心链路，但不做邮件读取和飞书发布：

- 品种：螺纹钢、盘螺、线材、H型钢、钢坯、优质碳素钢、冷镦钢、铁矿石、焦炭、废钢、铁水、纯镍、锰硅、黑钨精矿65、钼铁、片钒、铬铁、动力煤。
- 指标：螺纹钢社会库存、五大品种钢材社会库存、钢铁 PMI。
- 计算：18 周周度均价、环比、连续上涨/下跌、风险信号、机会信号、原料成本与成材价格剪刀差。
- 资讯：通过 Wind `financial_docs/get_financial_news` 获取近 14 天钢铁相关资讯；不读取邮箱。
- 图表：生成本地 SVG 折线图，Markdown 以相对路径引用。

## Windows 示例

```powershell
cd "C:\Users\lizhe\Documents\钢铁产业链周报\chain-report-cli-api"
& "C:\Users\lizhe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m chain_report.cli configure --provider openai-compatible --model "your-model-name" --base-url "https://your-endpoint/v1" --wind-node-bin "C:\Users\lizhe\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
.\run-report.ps1
```

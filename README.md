# ChainReport CLI

可替换底层模型的产业链研究报告命令行工具。

## 定位

- 独立 CLI 工具。
- 不读取邮件。
- 不替代现有每周五产业链周报定时任务。
- 第一版输出本地 Markdown 文件。

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

测试流程，不调用 Wind、不调用真实模型：

```bash
python3 -m chain_report.cli generate --sample-data --provider mock --non-interactive --no-save-keys
```

输出位置：

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

## Windows 示例

```powershell
cd "C:\Users\lizhe\Documents\钢铁产业链周报\chain-report-cli-api"
& "C:\Users\lizhe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m chain_report.cli configure --provider openai-compatible --model "your-model-name" --base-url "https://your-endpoint/v1" --wind-node-bin "C:\Users\lizhe\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
.\run-report.ps1
```

# ChainReport CLI

可替换底层模型的产业链研究报告命令行工具。

## 定位

- 这是一个新的独立 CLI 工具。
- 不读取邮件。
- 不替代现有每周五产业链周报定时任务。
- 第一版只输出本地 Markdown 文档。

## 运行

```bash
cd chain-report-cli
python3 -m chain_report.cli generate
```

测试流程（不调用 Wind / 不调用真实模型）：

```bash
python3 -m chain_report.cli generate --sample-data --provider mock --non-interactive
```

输出位置：

```text
outputs/steel-weekly/YYYY-MM-DD/report.md
```

## Key 管理

CLI 会优先读取当前目录 `.env` 或环境变量：

- `WIND_API_KEY`
- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `MINIMAX_CN_API_KEY`
- `MINIMAX_API_KEY`
- `OPENAI_COMPATIBLE_API_KEY`

缺失时会在命令行里询问，并默认保存到 `.env`（权限 600）。如果不想保存：

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

## 设计借鉴

参考 TradingAgents 的 CLI 思路：

- 交互式选择 provider / model
- 环境变量和 `.env` 优先
- 缺失 Key 时命令行询问
- provider、Key、base_url 集中映射
- 最终输出 Markdown 报告

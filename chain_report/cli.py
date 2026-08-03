"""Command line interface for ChainReport CLI."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from chain_report.config import DEFAULT_OUTPUT_DIR, ENV_PATH, PROVIDER_SPECS, load_env, set_env_value
from chain_report.generator import generate_report
from chain_report.providers.llm import LLMClient, LLMConfig
from chain_report.providers.wind import WindClient, WindConfig, sample_steel_snapshot


def ask_text(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("不能为空，请重新输入。")


def ask_secret(prompt: str, default_exists: bool = False, required: bool = True) -> str:
    hint = " [已配置，回车沿用]" if default_exists else ""
    while True:
        value = getpass.getpass(f"{prompt}{hint}: ").strip()
        if not value and default_exists:
            return ""
        if value or not required:
            return value
        print("不能为空，请重新输入。")


def ask_choice(prompt: str, choices: list[tuple[str, str]], default: Optional[str] = None) -> str:
    print(prompt)
    for idx, (key, label) in enumerate(choices, start=1):
        marker = "（默认）" if key == default else ""
        print(f"  [{idx}] {label} {marker}")
    while True:
        raw = input("> ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][0]
        keys = {key for key, _ in choices}
        if raw in keys:
            return raw
        print("选择无效，请输入序号或选项名。")


def ensure_key(env_var: str, label: str, non_interactive_value: Optional[str] = None, save: bool = True) -> Optional[str]:
    existing = os.environ.get(env_var)
    if non_interactive_value:
        if save:
            set_env_value(env_var, non_interactive_value)
        else:
            os.environ[env_var] = non_interactive_value
        return non_interactive_value
    if existing:
        value = ask_secret(f"请输入 {label}（{env_var}）", default_exists=True, required=False)
        return existing if value == "" else _save_or_env(env_var, value, save)
    value = ask_secret(f"请输入 {label}（{env_var}）", required=True)
    return _save_or_env(env_var, value, save)


def _save_or_env(env_var: str, value: str, save: bool) -> str:
    if save:
        set_env_value(env_var, value)
        print(f"已保存 {env_var} 到 {ENV_PATH}")
    else:
        os.environ[env_var] = value
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChainReport CLI - 可替换模型的产业链研究报告生成器")
    sub = parser.add_subparsers(dest="command")
    gen = sub.add_parser("generate", help="生成产业链研究报告 Markdown")
    gen.add_argument("--report-type", default="steel-weekly", choices=["steel-weekly"])
    gen.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    gen.add_argument("--date-range", default="最近三个月", help="传给 Wind 查询的日期范围描述")
    gen.add_argument("--provider", choices=list(PROVIDER_SPECS.keys()), default=None)
    gen.add_argument("--model", default=None)
    gen.add_argument("--base-url", default=None)
    gen.add_argument("--wind-key", default=None)
    gen.add_argument("--llm-key", default=None)
    gen.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    gen.add_argument("--sample-data", action="store_true", help="使用样例数据，不调用 Wind")
    gen.add_argument("--no-save-keys", action="store_true", help="不把输入的 Key 保存到 .env")
    gen.add_argument("--non-interactive", action="store_true", help="非交互模式，适合测试/脚本调用")
    return parser


def run_generate(args: argparse.Namespace) -> int:
    load_env()
    save_keys = not args.no_save_keys
    report_date = args.date or date.today().isoformat()
    report_type = args.report_type

    wind_key = None
    if args.non_interactive:
        provider = args.provider or "mock"
        if not args.sample_data:
            if args.wind_key or os.environ.get("WIND_API_KEY"):
                wind_key = ensure_key("WIND_API_KEY", "Wind API Key", args.wind_key, save=save_keys)
            else:
                # Wind MCP skill can also read its own saved global/local config.
                # In non-interactive smoke tests, allow that path instead of forcing
                # the key to be passed on the command line.
                print("未传 WIND_API_KEY；将尝试使用 Wind MCP 已保存配置。")
    else:
        print("\nChainReport CLI - 产业链研究报告生成器\n")
        if not args.sample_data:
            # Ryan 明确希望 CLI 一开始先确认 Wind Key，再选择模型。
            wind_key = ensure_key("WIND_API_KEY", "Wind API Key", args.wind_key, save=save_keys)
        else:
            print("Step 1: 使用样例数据，跳过 Wind API Key。")
        report_type = ask_choice("Step 2: 请选择报告类型", [("steel-weekly", "钢铁产业链研究周报")], report_type)
        report_date = ask_text("Step 3: 请输入报告日期 YYYY-MM-DD", report_date)
        provider = args.provider or ask_choice(
            "Step 4: 请选择底层模型 provider",
            [(key, spec["display"]) for key, spec in PROVIDER_SPECS.items()],
            "mock",
        )

    spec = PROVIDER_SPECS[provider]
    model = args.model or spec["default_model"]
    base_url = args.base_url or spec.get("base_url")
    if not args.non_interactive:
        model = ask_text("Step 5: 请输入模型名称", model)
        if provider == "openai-compatible":
            base_url = ask_text("Step 6: 请输入 OpenAI-compatible base_url", base_url, required=True)

    llm_key = None
    key_env = spec.get("key_env")
    if key_env and not spec.get("key_optional") and provider != "mock":
        if args.non_interactive and not args.llm_key and not os.environ.get(key_env):
            print(f"非交互模式缺少 {key_env}；请传 --llm-key，或使用 --provider mock。", file=sys.stderr)
            return 2
        llm_key = ensure_key(key_env, "模型 API Key", args.llm_key, save=save_keys)
    elif key_env and args.llm_key:
        llm_key = ensure_key(key_env, "模型 API Key", args.llm_key, save=save_keys)

    print("\n[1/3] 获取 Wind 数据...")
    if args.sample_data:
        wind_data = sample_steel_snapshot()
    else:
        wind_client = WindClient(WindConfig(api_key=wind_key))
        wind_data = wind_client.fetch_steel_snapshot(args.date_range)

    print("[2/3] 调用模型生成报告...")
    llm = LLMClient(LLMConfig(provider=provider, model=model, api_key=llm_key, base_url=base_url))
    output_path = generate_report(report_type, report_date, wind_data, llm, Path(args.output_dir))

    print(f"[3/3] Markdown 已生成：{output_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "generate"):
        if args.command is None:
            args = parser.parse_args(["generate", *(argv or [])])
        return run_generate(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

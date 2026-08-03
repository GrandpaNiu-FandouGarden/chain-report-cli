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

DEFAULT_PROVIDER_ENV = "CHAIN_REPORT_PROVIDER"
DEFAULT_MODEL_ENV = "CHAIN_REPORT_MODEL"
DEFAULT_BASE_URL_ENV = "CHAIN_REPORT_BASE_URL"
WIND_NODE_BIN_ENV = "CHAIN_REPORT_NODE_BIN"
WIND_MCP_DIR_ENV = "CHAIN_REPORT_WIND_MCP_DIR"


def ask_text(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("Value is required.")


def ask_secret(prompt: str, default_exists: bool = False, required: bool = True) -> str:
    hint = " [configured; press Enter to keep]" if default_exists else ""
    while True:
        value = getpass.getpass(f"{prompt}{hint}: ").strip()
        if not value and default_exists:
            return ""
        if value or not required:
            return value
        print("Value is required.")


def ask_choice(prompt: str, choices: list[tuple[str, str]], default: Optional[str] = None) -> str:
    print(prompt)
    for idx, (key, label) in enumerate(choices, start=1):
        marker = " (default)" if key == default else ""
        print(f"  [{idx}] {label}{marker}")
    while True:
        raw = input("> ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][0]
        keys = {key for key, _ in choices}
        if raw in keys:
            return raw
        print("Invalid choice. Enter a number or option name.")


def mask_secret(value: Optional[str]) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def save_or_env(env_var: str, value: str, save: bool) -> str:
    if save:
        set_env_value(env_var, value)
        print(f"Saved {env_var} to {ENV_PATH}")
    else:
        os.environ[env_var] = value
    return value


def resolve_key(
    env_var: str,
    label: str,
    cli_value: Optional[str] = None,
    save: bool = True,
    interactive: bool = True,
    required: bool = True,
) -> Optional[str]:
    existing = os.environ.get(env_var)
    if cli_value:
        return save_or_env(env_var, cli_value, save)
    if not interactive:
        if existing:
            return existing
        if required:
            print(f"Missing {env_var}. Pass --llm-key/--wind-key or run configure first.", file=sys.stderr)
        return None
    if existing:
        value = ask_secret(f"Enter {label} ({env_var})", default_exists=True, required=False)
        return existing if value == "" else save_or_env(env_var, value, save)
    value = ask_secret(f"Enter {label} ({env_var})", required=required)
    if not value:
        return None
    return save_or_env(env_var, value, save)


def configured_provider(default: str = "mock") -> str:
    provider = os.environ.get(DEFAULT_PROVIDER_ENV, default)
    return provider if provider in PROVIDER_SPECS else default


def add_shared_generate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=list(PROVIDER_SPECS.keys()), default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--wind-key", default=None)
    parser.add_argument("--wind-node-bin", default=None, help="Node.js executable used by Wind MCP")
    parser.add_argument("--wind-mcp-dir", default=None, help="Wind MCP skill directory")
    parser.add_argument("--llm-key", default=None)
    parser.add_argument("--no-save-keys", action="store_true", help="Do not save entered keys to .env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChainReport CLI - configurable industry-chain report generator")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Generate a Markdown industry-chain research report")
    gen.add_argument("--report-type", default="steel-weekly", choices=["steel-weekly"])
    gen.add_argument("--date", default=None, help="Report date YYYY-MM-DD; default: today")
    gen.add_argument("--date-range", default="最近三个月", help="Date range text passed to Wind queries")
    add_shared_generate_args(gen)
    gen.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    gen.add_argument("--sample-data", action="store_true", help="Use sample data and skip Wind")
    gen.add_argument("--non-interactive", action="store_true", help="Non-interactive mode for scheduled jobs")

    cfg = sub.add_parser("configure", help="Save Wind and model configuration to .env")
    add_shared_generate_args(cfg)
    cfg.add_argument("--non-interactive", action="store_true", help="Fail instead of prompting for missing values")

    sub.add_parser("config-show", help="Show current configuration with secrets masked")
    return parser


def run_configure(args: argparse.Namespace) -> int:
    load_env()
    save = not args.no_save_keys
    interactive = not args.non_interactive

    provider_default = configured_provider("openai-compatible")
    provider = args.provider
    if not provider and interactive:
        provider = ask_choice(
            "Choose default model provider",
            [(key, spec["display"]) for key, spec in PROVIDER_SPECS.items()],
            provider_default,
        )
    provider = provider or provider_default
    if provider not in PROVIDER_SPECS:
        print(f"Unsupported provider: {provider}", file=sys.stderr)
        return 2

    spec = PROVIDER_SPECS[provider]
    model = args.model or os.environ.get(DEFAULT_MODEL_ENV) or spec["default_model"]
    base_url = args.base_url or os.environ.get(DEFAULT_BASE_URL_ENV) or spec.get("base_url")

    if interactive:
        model = ask_text("Default model", model)
        if provider == "openai-compatible":
            base_url = ask_text("OpenAI-compatible base_url", base_url, required=True)
        elif spec.get("base_url"):
            base_url = ask_text("Provider base_url", base_url, required=False)

    if save:
        set_env_value(DEFAULT_PROVIDER_ENV, provider)
        set_env_value(DEFAULT_MODEL_ENV, model)
        if base_url:
            set_env_value(DEFAULT_BASE_URL_ENV, base_url)
        if args.wind_node_bin:
            set_env_value(WIND_NODE_BIN_ENV, args.wind_node_bin)
        if args.wind_mcp_dir:
            set_env_value(WIND_MCP_DIR_ENV, args.wind_mcp_dir)

    wind_key_required = bool(args.wind_key) or interactive
    resolve_key("WIND_API_KEY", "Wind API Key", args.wind_key, save=save, interactive=interactive, required=wind_key_required)

    key_env = spec.get("key_env")
    if key_env:
        key_required = not spec.get("key_optional")
        resolve_key(key_env, "model API Key", args.llm_key, save=save, interactive=interactive, required=key_required)

    print(f"Configuration saved to {ENV_PATH}" if save else "Configuration loaded into current process only.")
    return 0


def run_config_show(_: argparse.Namespace) -> int:
    load_env()
    provider = configured_provider("(not set)")
    spec = PROVIDER_SPECS.get(provider, {})
    key_env = spec.get("key_env")
    values = {
        DEFAULT_PROVIDER_ENV: provider,
        DEFAULT_MODEL_ENV: os.environ.get(DEFAULT_MODEL_ENV),
        DEFAULT_BASE_URL_ENV: os.environ.get(DEFAULT_BASE_URL_ENV),
        "WIND_API_KEY": mask_secret(os.environ.get("WIND_API_KEY")),
        WIND_NODE_BIN_ENV: os.environ.get(WIND_NODE_BIN_ENV),
        WIND_MCP_DIR_ENV: os.environ.get(WIND_MCP_DIR_ENV),
    }
    if key_env:
        values[key_env] = mask_secret(os.environ.get(key_env))
    print(f"Config file: {ENV_PATH}")
    for key, value in values.items():
        print(f"{key}={value or '(not set)'}")
    return 0


def run_generate(args: argparse.Namespace) -> int:
    load_env()
    save_keys = not args.no_save_keys
    interactive = not args.non_interactive
    report_date = args.date or date.today().isoformat()
    report_type = args.report_type

    provider = args.provider or configured_provider("mock")
    spec = PROVIDER_SPECS[provider]
    model = args.model or os.environ.get(DEFAULT_MODEL_ENV) or spec["default_model"]
    base_url = args.base_url or os.environ.get(DEFAULT_BASE_URL_ENV) or spec.get("base_url")

    wind_key = None
    if interactive:
        print("\nChainReport CLI - industry-chain report generator\n")
        if not args.sample_data:
            wind_key = resolve_key("WIND_API_KEY", "Wind API Key", args.wind_key, save=save_keys, interactive=True, required=True)
        else:
            print("Step 1: using sample data; skipping Wind API Key.")
        report_type = ask_choice("Step 2: choose report type", [("steel-weekly", "Steel industry-chain weekly report")], report_type)
        report_date = ask_text("Step 3: report date YYYY-MM-DD", report_date)
        provider = args.provider or ask_choice(
            "Step 4: choose model provider",
            [(key, item["display"]) for key, item in PROVIDER_SPECS.items()],
            provider,
        )
        spec = PROVIDER_SPECS[provider]
        model = ask_text("Step 5: model name", model)
        if provider == "openai-compatible":
            base_url = ask_text("Step 6: OpenAI-compatible base_url", base_url, required=True)
    elif not args.sample_data:
        wind_key = resolve_key("WIND_API_KEY", "Wind API Key", args.wind_key, save=save_keys, interactive=False, required=False)
        if not wind_key:
            print("No WIND_API_KEY passed; trying Wind MCP saved configuration.")

    llm_key = None
    key_env = spec.get("key_env")
    if key_env and provider != "mock":
        required = not spec.get("key_optional")
        llm_key = resolve_key(key_env, "model API Key", args.llm_key, save=save_keys, interactive=interactive, required=required)
        if required and not llm_key:
            return 2
    elif key_env and args.llm_key:
        llm_key = resolve_key(key_env, "model API Key", args.llm_key, save=save_keys, interactive=interactive, required=False)

    print("\n[1/3] Fetching Wind data...")
    if args.sample_data:
        wind_data = sample_steel_snapshot()
    else:
        wind_node_bin = args.wind_node_bin or os.environ.get(WIND_NODE_BIN_ENV) or WindConfig.node_bin
        wind_mcp_dir = args.wind_mcp_dir or os.environ.get(WIND_MCP_DIR_ENV)
        wind_client = WindClient(
            WindConfig(
                api_key=wind_key,
                node_bin=wind_node_bin,
                mcp_dir=Path(wind_mcp_dir) if wind_mcp_dir else WindConfig.mcp_dir,
            )
        )
        wind_data = wind_client.fetch_steel_snapshot(args.date_range)

    print("[2/3] Calling model...")
    llm = LLMClient(LLMConfig(provider=provider, model=model, api_key=llm_key, base_url=base_url))
    output_path = generate_report(report_type, report_date, wind_data, llm, Path(args.output_dir))

    print(f"[3/3] Markdown generated: {output_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "generate"):
        if args.command is None:
            args = parser.parse_args(["generate", *(argv or [])])
        return run_generate(args)
    if args.command == "configure":
        return run_configure(args)
    if args.command == "config-show":
        return run_config_show(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

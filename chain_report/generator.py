"""Report generation orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from chain_report.output import write_markdown
from chain_report.providers.llm import LLMClient
from chain_report.templates import build_steel_weekly_prompt


def generate_report(
    report_type: str,
    report_date: str,
    wind_data: Dict[str, Any],
    llm: LLMClient,
    output_dir: Path,
) -> Path:
    if report_type != "steel-weekly":
        raise ValueError(f"Unsupported report_type: {report_type}")
    prompt = build_steel_weekly_prompt(report_date, wind_data)
    content = llm.generate(prompt)
    return write_markdown(content, output_dir, report_type, report_date)

"""Markdown output helpers."""
from __future__ import annotations

from pathlib import Path


def write_markdown(content: str, output_dir: Path, report_type: str, report_date: str) -> Path:
    target_dir = output_dir / report_type / report_date
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "report.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path

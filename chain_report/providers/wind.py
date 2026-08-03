"""Wind data access wrapper for ChainReport CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_WIND_MCP_DIR = Path(os.environ.get("CHAIN_REPORT_WIND_MCP_DIR", Path.home() / ".agents" / "skills" / "wind-mcp-skill"))
DEFAULT_NODE = os.environ.get("CHAIN_REPORT_NODE_BIN") or shutil.which("node") or "node"


@dataclass
class WindConfig:
    api_key: str | None = None
    mcp_dir: Path = DEFAULT_WIND_MCP_DIR
    node_bin: str = DEFAULT_NODE
    timeout: int = 120


class WindError(RuntimeError):
    pass


class WindClient:
    def __init__(self, config: WindConfig):
        self.config = config
        if config.api_key:
            os.environ["WIND_API_KEY"] = config.api_key

    @property
    def cli_path(self) -> Path:
        return self.config.mcp_dir / "scripts" / "cli.mjs"

    def available(self) -> bool:
        return self.cli_path.exists()

    def query(self, question: str) -> Dict[str, Any]:
        if not self.available():
            raise WindError(f"Wind MCP CLI not found: {self.cli_path}")
        cmd = [
            self.config.node_bin,
            str(self.cli_path),
            "call",
            "analytics_data",
            "get_financial_data",
            json.dumps({"question": question}, ensure_ascii=False),
        ]
        env = os.environ.copy()
        if self.config.api_key:
            env["WIND_API_KEY"] = self.config.api_key
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout,
                cwd=str(self.config.mcp_dir),
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise WindError(f"Wind query timeout: {question}") from e
        if result.returncode != 0:
            raise WindError(result.stderr.strip() or result.stdout.strip() or "Wind CLI failed")
        return self._parse_envelope(result.stdout)

    def _parse_envelope(self, stdout: str) -> Dict[str, Any]:
        outer = json.loads(stdout.strip())
        if outer.get("isError"):
            raise WindError(json.dumps(outer, ensure_ascii=False)[:1000])
        content = outer.get("content") or []
        if not content:
            return {"raw": outer, "tables": []}
        text = content[0].get("text", "")
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": text, "tables": []}
        return {"raw": inner, "tables": self._extract_tables(inner)}

    def _extract_tables(self, inner: Dict[str, Any]) -> List[Dict[str, Any]]:
        steps = inner.get("data", {}).get("data", [])
        tables = []
        for step in steps:
            columns = step.get("columns") or []
            rows = step.get("rows") or []
            if columns and rows:
                tables.append({"columns": columns, "rows": rows})
        return tables

    def fetch_steel_snapshot(self, date_range: str) -> Dict[str, Any]:
        queries = [
            f"螺纹钢现货价格 日度 {date_range}",
            f"铁矿石现货价格 日度数据 {date_range}",
            f"焦炭现货价格 周度 {date_range}",
            f"五大品种钢材社会库存 周度 {date_range}",
            "中国钢铁行业PMI 月度 今年",
        ]
        results = {}
        for q in queries:
            try:
                results[q] = self.query(q)
            except Exception as e:
                results[q] = {"error": str(e)}
        return results


def sample_steel_snapshot() -> Dict[str, Any]:
    return {
        "说明": "样例数据，仅用于 CLI 流程验证；真实运行请提供 Wind Key 并关闭 --sample-data。",
        "螺纹钢现货价格 日度 最近三个月": {"latest": 3250, "unit": "元/吨", "trend": "震荡"},
        "铁矿石现货价格 日度数据 最近三个月": {"latest": 780, "unit": "元/吨", "trend": "偏强"},
        "五大品种钢材社会库存 周度 最近三个月": {"latest": 1250, "unit": "万吨", "trend": "小幅去库"},
    }

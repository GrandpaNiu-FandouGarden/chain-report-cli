"""Deterministic steel weekly report pipeline.

This module mirrors the useful parts of the original OpenClaw automation:
multi-commodity Wind data, weekly aggregation, risk/business rules, charts and
local Markdown output. It deliberately does not read email or publish to Lark.
"""
from __future__ import annotations

import html
import math
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from chain_report.providers.wind import WindClient

WEEKS_HISTORY = 18

COMMODITIES: Dict[str, Dict[str, str]] = {
    "螺纹钢": {"query": "螺纹钢现货价格 日度 {date_range}", "unit": "元/吨", "group": "成材"},
    "盘螺": {"query": "盘螺现货价格 日度 {date_range}", "unit": "元/吨", "group": "成材"},
    "线材": {"query": "线材现货价格 日度 {date_range}", "unit": "元/吨", "group": "成材"},
    "H型钢": {"query": "H型钢国内现货价格 日度 {date_range}", "unit": "元/吨", "group": "成材"},
    "钢坯": {"query": "钢坯现货价格 日度 {date_range}", "unit": "元/吨", "group": "坯料"},
    "优质碳素钢": {"query": "优质碳素结构钢现货价格 {date_range}", "unit": "元/吨", "group": "坯料"},
    "冷镦钢": {"query": "冷墩钢现货价格 冷镦钢 上海 日度 {date_range}", "unit": "元/吨", "group": "坯料"},
    "铁矿石": {"query": "铁矿石现货价格 日度数据 {date_range}", "unit": "元/吨", "group": "原料"},
    "焦炭": {"query": "焦炭现货价格 周度 {date_range}", "unit": "元/吨", "group": "原料"},
    "废钢": {"query": "废钢现货价格 日度 {date_range}", "unit": "元/吨", "group": "原料"},
    "铁水": {"query": "生铁现货价格 日度 {date_range}", "unit": "元/吨", "group": "原料"},
    "纯镍": {"query": "纯镍盘面价格 期货 日度 {date_range}", "unit": "万元/吨", "group": "合金"},
    "锰硅": {"query": "锰硅现货价格 日度 {date_range}", "unit": "元/吨", "group": "合金"},
    "黑钨精矿65": {"query": "黑钨精矿65现货价格 日度 {date_range}", "unit": "万元/吨", "group": "合金"},
    "钼铁": {"query": "钼铁45-50现货价格 日度 {date_range}", "unit": "万元/基吨", "group": "合金"},
    "片钒": {"query": "五氧化二钒 片钒 钒铁 国内价格 {year}年", "unit": "美元/吨", "group": "合金"},
    "铬铁": {"query": "铬铁 国内价格 Cr含量30 {year}年", "unit": "美元/千克", "group": "合金"},
    "动力煤": {"query": "动力煤现货价格 5500大卡 日度 {date_range}", "unit": "元/吨", "group": "能源"},
}

INDICATORS: Dict[str, Dict[str, str]] = {
    "螺纹钢社库": {"query": "螺纹钢社会库存 周度 {date_range}", "unit": "万吨", "group": "供需"},
    "五大品种库存": {"query": "五大品种钢材社会库存 周度 {date_range}", "unit": "万吨", "group": "供需"},
    "钢铁PMI": {"query": "中国钢铁行业PMI 月度 {year}年", "unit": "指数", "group": "供需"},
}

GROUP_ORDER = [
    ("成材", "成材端", ["螺纹钢", "盘螺", "线材", "H型钢"], "成材价格直接影响下游客户库存货值和销售回款，价格下行阶段需关注库存减值与回款节奏。"),
    ("坯料", "坯料与中间品", ["钢坯", "优质碳素钢", "冷镦钢"], "坯料及中间品连接原料与成材，价格变化会传导至加工企业采购成本和阶段性备货需求。"),
    ("原料", "原料端", ["铁矿石", "焦炭", "废钢", "铁水"], "原料价格波动会影响上游贸易商采购资金占用，持续上涨时融资需求通常更容易放大。"),
    ("合金", "合金与特钢", ["纯镍", "锰硅", "黑钨精矿65", "钼铁", "片钒", "铬铁"], "合金及特钢原料价格弹性较大，应重点关注贸易商库存估值、订单真实性和价格剧烈波动带来的履约压力。"),
    ("能源", "能源端", ["动力煤"], "能源价格影响全链条生产成本，是判断钢厂利润和后续采购节奏的重要辅助指标。"),
]


@dataclass
class RawSeries:
    query: str
    points: List[Tuple[str, float]]
    error: Optional[str] = None


def default_date_range() -> str:
    today = datetime.now()
    start = today - timedelta(weeks=WEEKS_HISTORY)
    return f"{start.year}年{start.month}月到{today.month}月"


def parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue
    return None


def parse_wind_points(result: Dict[str, Any], sum_numeric_columns: bool = False) -> List[Tuple[str, float]]:
    tables = result.get("tables") or []
    if not tables:
        return []
    table = tables[0]
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    date_idx = None
    best_value_idx = None
    fallback_value_idx = None
    for idx, col in enumerate(columns):
        name = col.get("name", "")
        if col.get("type") == "date" or "日期" in name:
            date_idx = idx
        if col.get("type") == "number":
            non_null = sum(1 for row in rows if len(row) > idx and row[idx] is not None)
            if not non_null:
                continue
            if any(k in name for k in ["平均价", "价格", "收盘", "指数", "现货", "市场价", "平仓价"]):
                best_value_idx = idx
                break
            if fallback_value_idx is None:
                fallback_value_idx = idx
    value_idx = best_value_idx if best_value_idx is not None else fallback_value_idx
    if date_idx is None or value_idx is None:
        return []
    points: List[Tuple[str, float]] = []
    for row in rows:
        if len(row) <= date_idx:
            continue
        dt = parse_date(str(row[date_idx]))
        if not dt:
            continue
        try:
            if sum_numeric_columns:
                numeric_indexes = [
                    idx
                    for idx, col in enumerate(columns)
                    if col.get("type") == "number" and any(len(r) > idx and r[idx] is not None for r in rows)
                ]
                values = [float(row[idx]) for idx in numeric_indexes if len(row) > idx and row[idx] is not None]
                if values:
                    points.append((dt.strftime("%Y-%m-%d"), sum(values)))
            else:
                raw_value = row[value_idx]
                if raw_value is not None:
                    points.append((dt.strftime("%Y-%m-%d"), float(raw_value)))
        except (TypeError, ValueError):
            continue
    return points


def aggregate_weekly(points: Iterable[Tuple[str, float]]) -> Dict[Tuple[int, int], float]:
    buckets: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    for date_str, value in points:
        dt = parse_date(date_str)
        if not dt:
            continue
        iso = dt.isocalendar()
        buckets[(iso.year, iso.week)].append(value)
    result = {week: round(sum(values) / len(values), 2) for week, values in sorted(buckets.items()) if values}
    if len(result) > WEEKS_HISTORY:
        keys = list(result.keys())[-WEEKS_HISTORY:]
        result = {key: result[key] for key in keys}
    return result


def count_direction(weekly: Dict[Tuple[int, int], float], direction: str) -> int:
    values = list(weekly.values())
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if direction == "down" and values[i] < values[i - 1]:
            count += 1
        elif direction == "up" and values[i] > values[i - 1]:
            count += 1
        else:
            break
    return count


def calculate_changes(weekly: Dict[Tuple[int, int], float]) -> Dict[str, Any]:
    if not weekly:
        return {"weekly_data": {}, "latest_value": None, "prev_value": None, "wow_pct": None, "wow_abs": None, "direction": "unknown"}
    values = list(weekly.values())
    latest = values[-1]
    prev = values[-2] if len(values) >= 2 else None
    if prev in (None, 0):
        wow_abs = None
        wow_pct = None
        direction = "unknown"
    else:
        wow_abs = round(latest - prev, 2)
        wow_pct = round((latest - prev) / prev * 100, 1)
        direction = "up" if wow_abs > 0 else "down" if wow_abs < 0 else "flat"
    return {
        "weekly_data": weekly,
        "latest_value": latest,
        "prev_value": prev,
        "wow_abs": wow_abs,
        "wow_pct": wow_pct,
        "direction": direction,
        "down_weeks": count_direction(weekly, "down"),
        "up_weeks": count_direction(weekly, "up"),
    }


def fetch_raw_series(wind: WindClient, date_range: Optional[str] = None) -> Dict[str, RawSeries]:
    date_text = date_range or default_date_range()
    year_text = str(date.today().year)
    raw: Dict[str, RawSeries] = {}
    specs = {**COMMODITIES, **INDICATORS}
    for name, spec in specs.items():
        query = spec["query"].format(date_range=date_text, year=year_text)
        try:
            result = wind.query(query)
            raw[name] = RawSeries(query=query, points=parse_wind_points(result))
        except Exception as exc:
            raw[name] = RawSeries(query=query, points=[], error=str(exc))
    return raw


def build_risk_signal(name: str, item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    down_weeks = item.get("down_weeks") or 0
    wow_pct = item.get("wow_pct")
    if down_weeks >= 4:
        return {"level": "severe", "message": f"{name}连续{down_weeks}周下跌，建议暂缓对相关客户新增额度审批"}
    if down_weeks >= 3:
        return {"level": "moderate", "message": f"{name}连续{down_weeks}周下跌，建议关注相关客户库存周转和还款节奏"}
    if wow_pct is not None and wow_pct <= -5:
        return {"level": "moderate", "message": f"{name}单周下跌{abs(wow_pct):.1f}%，需复核库存货值和保证金覆盖"}
    return None


def build_opportunity_signal(name: str, item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    up_weeks = item.get("up_weeks") or 0
    if up_weeks >= 3:
        return {"message": f"{name}连续{up_weeks}周上行，低库存快周转客户可能出现采购周转需求"}
    return None


def calculate_scissors(processed: Dict[str, Dict[str, Any]]) -> Dict[Tuple[int, int], float]:
    raw_names = ["铁矿石", "焦炭", "废钢"]
    product_names = ["螺纹钢", "盘螺", "线材", "H型钢"]
    raw_weeks = set().union(*(processed.get(n, {}).get("weekly_data", {}).keys() for n in raw_names))
    product_weeks = set().union(*(processed.get(n, {}).get("weekly_data", {}).keys() for n in product_names))
    result: Dict[Tuple[int, int], float] = {}
    for week in sorted(raw_weeks & product_weeks):
        raw_values = [processed[n]["weekly_data"][week] for n in raw_names if week in processed.get(n, {}).get("weekly_data", {})]
        product_values = [processed[n]["weekly_data"][week] for n in product_names if week in processed.get(n, {}).get("weekly_data", {})]
        if raw_values and product_values:
            raw_avg = sum(raw_values) / len(raw_values)
            product_avg = sum(product_values) / len(product_values)
            if raw_avg:
                result[week] = round((product_avg - raw_avg) / raw_avg * 100, 2)
    return result


def fetch_exchange_rate(wind: WindClient) -> float:
    try:
        result = wind.query("美元兑人民币汇率中间价 今天")
        points = parse_wind_points(result)
        if points:
            value = points[-1][1]
            if 5.0 < value < 9.0:
                return value
    except Exception:
        pass
    return 7.25


def normalize_wind_query(text: str) -> str:
    return "".join(str(text).split())


def fetch_wind_news(wind: WindClient) -> Dict[str, List[Dict[str, Any]]]:
    queries = [
        "钢铁行业最新政策动态",
        "钢铁市场供需分析",
        "钢厂减产限产最新",
        "钢铁行业保理 货权 回款 风险",
    ]
    items: List[Dict[str, Any]] = []
    seen = set()
    cutoff = datetime.now() - timedelta(days=14)
    for query in queries:
        try:
            result = wind.call_tool("financial_docs", "get_financial_news", {"query": normalize_wind_query(query), "top_k": 5})
            raw = result.get("raw", {})
            data = raw.get("data", {}) if isinstance(raw, dict) else {}
            candidates = data.get("items", []) if isinstance(data, dict) else []
            for item in candidates:
                title = item.get("title") or ""
                if not title or title in seen:
                    continue
                item_date = item.get("date") or ""
                dt = parse_date(item_date)
                if dt and dt < cutoff:
                    continue
                seen.add(title)
                items.append(
                    {
                        "title": title,
                        "content": item.get("content") or "",
                        "date": item_date,
                        "source": item.get("source") or "Wind资讯",
                        "url": item.get("url") or "",
                    }
                )
        except Exception:
            continue
    items.sort(key=lambda x: (len(x.get("content") or ""), x.get("date") or ""), reverse=True)
    return {"news": items[:10]}


def process_raw_data(raw: Dict[str, RawSeries], exchange_rate: float) -> Dict[str, Any]:
    commodities: Dict[str, Dict[str, Any]] = {}
    risks: List[Dict[str, str]] = []
    opportunities: List[Dict[str, str]] = []
    for name, series in raw.items():
        points = series.points
        display_unit = (COMMODITIES.get(name) or INDICATORS.get(name, {})).get("unit", "")
        if name == "片钒" and points:
            points = [(d, v * exchange_rate) for d, v in points]
            display_unit = "元/吨"
        elif name == "铬铁" and points:
            points = [(d, v * 1000 * exchange_rate) for d, v in points]
            display_unit = "元/吨"
        weekly = aggregate_weekly(points)
        item = calculate_changes(weekly)
        spec = COMMODITIES.get(name) or INDICATORS.get(name, {})
        item.update({"unit": display_unit, "group": spec.get("group", ""), "query": series.query, "error": series.error})
        commodities[name] = item
        if name in COMMODITIES:
            risk = build_risk_signal(name, item)
            opp = build_opportunity_signal(name, item)
            if risk:
                risks.append(risk)
            if opp:
                opportunities.append(opp)
    return {
        "commodities": commodities,
        "risks": risks,
        "opportunities": opportunities,
        "scissors_diff": calculate_scissors(commodities),
        "exchange_rate": exchange_rate,
    }


def week_label(week: Tuple[int, int]) -> str:
    return f"{week[0]}W{week[1]:02d}"


def write_svg_line_chart(title: str, series: Dict[str, Dict[Tuple[int, int], float]], unit: str, path: Path) -> Optional[Path]:
    series = {name: data for name, data in series.items() if data}
    if not series:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    weeks = sorted(set().union(*(data.keys() for data in series.values())))
    if len(weeks) < 2:
        return None
    values = [v for data in series.values() for v in data.values() if isinstance(v, (int, float))]
    low, high = min(values), max(values)
    if math.isclose(low, high):
        low -= 1
        high += 1
    width, height = 980, 420
    left, right, top, bottom = 72, 30, 48, 62
    plot_w, plot_h = width - left - right, height - top - bottom
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b"]

    def x_pos(i: int) -> float:
        return left + i * plot_w / max(len(weeks) - 1, 1)

    def y_pos(value: float) -> float:
        return top + (high - value) * plot_h / (high - low)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="28" font-size="20" font-family="Microsoft YaHei, Arial" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{left}" y="{height-18}" font-size="12" font-family="Arial" fill="#555">单位：{html.escape(unit)}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#999"/>',
        f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#999"/>',
    ]
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = top + plot_h * frac
        value = high - (high - low) * frac
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#eee"/>')
        lines.append(f'<text x="8" y="{y+4:.1f}" font-size="11" font-family="Arial" fill="#666">{value:.1f}</text>')
    step = max(len(weeks) // 6, 1)
    for i, week in enumerate(weeks):
        if i % step == 0 or i == len(weeks) - 1:
            lines.append(f'<text x="{x_pos(i)-18:.1f}" y="{top+plot_h+20}" font-size="11" font-family="Arial" fill="#666">{week_label(week)}</text>')
    for idx, (name, data) in enumerate(series.items()):
        color = colors[idx % len(colors)]
        points = []
        for i, week in enumerate(weeks):
            if week in data:
                points.append(f"{x_pos(i):.1f},{y_pos(data[week]):.1f}")
        if len(points) >= 2:
            lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.4"/>')
            lines.append(f'<text x="{left + (idx % 3) * 260}" y="{height-38 - (idx // 3)*16}" font-size="12" font-family="Microsoft YaHei, Arial" fill="{color}">{html.escape(name)}</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_charts(processed: Dict[str, Any], report_dir: Path) -> Dict[str, str]:
    charts_dir = report_dir / "charts"
    commodities = processed["commodities"]
    charts: Dict[str, str] = {}
    for group_key, title, names, _ in GROUP_ORDER:
        data = {name: commodities.get(name, {}).get("weekly_data", {}) for name in names}
        unit = next((commodities.get(name, {}).get("unit", "") for name in names if commodities.get(name, {}).get("unit")), "")
        path = write_svg_line_chart(f"{title}价格对比", data, unit, charts_dir / f"overview_{group_key}.svg")
        if path:
            charts[f"overview_{group_key}"] = f"charts/{path.name}"
    for name in COMMODITIES:
        data = commodities.get(name, {}).get("weekly_data", {})
        unit = commodities.get(name, {}).get("unit", COMMODITIES[name]["unit"])
        path = write_svg_line_chart(f"{name} 18周走势", {name: data}, unit, charts_dir / f"commodity_{name}.svg")
        if path:
            charts[f"commodity_{name}"] = f"charts/{path.name}"
    inventory = {name: commodities.get(name, {}).get("weekly_data", {}) for name in ("螺纹钢社库", "五大品种库存")}
    path = write_svg_line_chart("库存趋势", inventory, "万吨", charts_dir / "inventory.svg")
    if path:
        charts["inventory"] = f"charts/{path.name}"
    path = write_svg_line_chart("原料成本与成材价格剪刀差", {"剪刀差": processed.get("scissors_diff", {})}, "%", charts_dir / "scissors_diff.svg")
    if path:
        charts["scissors_diff"] = f"charts/{path.name}"
    return charts


def direction_text(direction: str) -> str:
    return {"up": "上行", "down": "回落", "flat": "持平"}.get(direction, "-")


def position_text(latest: float, low: float, high: float) -> str:
    if math.isclose(low, high):
        return "中位"
    ratio = (latest - low) / (high - low)
    if ratio <= 0.33:
        return "低位"
    if ratio >= 0.67:
        return "高位"
    return "中位"


def build_price_table(commodities: Dict[str, Any]) -> str:
    lines = ["| 品种 | 最新周均价 | 环比变动 | 方向 |", "|---|---:|---:|---|"]
    for name in COMMODITIES:
        item = commodities.get(name, {})
        latest = item.get("latest_value")
        if latest is None:
            lines.append(f"| {name} | 数据暂缺 | - | - |")
            continue
        wow = item.get("wow_pct")
        wow_text = f"{wow:+.1f}%" if wow is not None else "-"
        lines.append(f"| {name} | {latest:,.2f} {item.get('unit','')} | {wow_text} | {direction_text(item.get('direction'))} |")
    return "\n".join(lines) + "\n"


def summarize_group(names: List[str], commodities: Dict[str, Any]) -> str:
    up = [n for n in names if commodities.get(n, {}).get("direction") == "up"]
    down = [n for n in names if commodities.get(n, {}).get("direction") == "down"]
    flat = [n for n in names if commodities.get(n, {}).get("direction") == "flat"]
    parts = []
    if up:
        parts.append("、".join(up) + "上涨")
    if down:
        parts.append("、".join(down) + "回落")
    if flat:
        parts.append("、".join(flat) + "基本持平")
    return "本组品种本周表现为" + "，".join(parts) + "。" if parts else "本组品种本周有效数据不足。"


def trend_sentence(name: str, item: Dict[str, Any]) -> str:
    latest = item.get("latest_value")
    if latest is None:
        return f"{name}本周数据暂缺，暂不作方向判断。"
    wow_pct = item.get("wow_pct")
    wow_abs = item.get("wow_abs")
    unit = item.get("unit", "")
    base = f"{name}本周周均价为{latest:,.2f}{unit}"
    if wow_pct is not None:
        base += f"，环比{wow_pct:+.1f}%（{wow_abs:+,.2f}{unit}）"
    weekly = item.get("weekly_data", {}) or {}
    range_sentence = ""
    if len(weekly) >= 3:
        values = list(weekly.values())
        low, high = min(values), max(values)
        range_sentence = f"，最新价格处于近{len(values)}周区间的{position_text(latest, low, high)}"
    down_weeks = item.get("down_weeks") or 0
    up_weeks = item.get("up_weeks") or 0
    if down_weeks >= 3:
        base += f"。该品种已连续{down_weeks}周回落{range_sentence}，需重点观察库存跌价和订单回款压力。"
    elif up_weeks >= 3:
        base += f"。该品种已连续{up_weeks}周上行{range_sentence}，短期资金占用和补库需求可能同步抬升。"
    elif item.get("direction") == "down":
        base += f"。本周价格回落，但尚未形成明显连续趋势{range_sentence}。"
    elif item.get("direction") == "up":
        base += f"。本周价格回升{range_sentence}，需结合订单真实性和销售回款判断融资合理性。"
    else:
        base += f"。本周价格波动较小{range_sentence}，短期更多体现震荡特征。"
    return base


def business_sentence(name: str, group: str) -> str:
    if group in ("成材", "坯料"):
        return f"对下游客户而言，{name}价格变化会影响库存货值和采购节奏，应重点核查库存周转、下游订单和回款闭环。"
    if group == "原料":
        return f"对上游贸易商而言，{name}价格回落时应关注存货跌价和补保证金压力，价格上行时应关注采购资金占用。"
    if group == "合金":
        return f"{name}属于波动弹性较高的合金/特钢相关品种，业务判断应优先核实货权、库存位置、合同锁价安排和回款路径。"
    return f"{name}价格主要作为成本和需求辅助信号，需结合客户订单和回款路径判断。"


def build_core_summary(processed: Dict[str, Any], news_data: Optional[Dict[str, Any]] = None) -> str:
    commodities = processed["commodities"]
    group_parts = []
    for group_key, title, names, _ in GROUP_ORDER:
        text = summarize_group(names, commodities)
        if "有效数据不足" not in text:
            group_parts.append(f"{title}方面，{text.removeprefix('本组品种本周表现为')}")
    scissors = processed.get("scissors_diff", {})
    margin = ""
    if len(scissors) >= 2:
        values = list(scissors.values())
        margin = f"从产业链利润 proxy 看，原料成本与成材价格剪刀差本周{'扩大' if values[-1] > values[-2] else '收窄'}，最新值为{values[-1]:.2f}%。"
    authority = summarize_authority_context(news_data or {})
    business = authority + "业务层面，本周建议以新增机会识别为主、风险边界偏保守，优先筛选货权可控、回款可闭环、库存周转快的客户。"
    return "".join(group_parts) + margin + business + "\n"


def collect_authority_items(news_data: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    items = []
    for item in news_data.get("news", []) or []:
        items.append(
            {
                "title": item.get("title") or "Wind资讯",
                "content": item.get("content") or "",
                "date": item.get("date") or "日期未返回",
                "source": item.get("source") or "Wind资讯",
                "url": item.get("url") or "",
            }
        )
    items.sort(key=lambda x: (bool(x.get("url")), len(x.get("content") or "")), reverse=True)
    return items[:limit]


def extract_key_signal(text: str) -> str:
    signals = []
    for kw in ["库存", "利润", "减产", "限产", "需求", "供需", "焦炭", "铁矿", "螺纹", "出口", "政策", "PMI"]:
        if kw in text and kw not in signals:
            signals.append(kw)
    return "、".join(signals[:6]) + "相关信号需跟踪。" if signals else "未提取到明确量化数据，作为行业情绪和方向性信息参考。"


def infer_authority_business_meaning(text: str) -> str:
    if any(kw in text for kw in ["减产", "限产", "检修"]):
        return "限产或检修信息可能影响供给节奏和短期价格预期，但不能直接等同于客户融资机会；应核实客户是否有真实订单、锁价安排和可控货权。"
    if any(kw in text for kw in ["库存", "累库", "去库"]):
        return "库存变化会影响贸易商周转和跌价风险，应优先核查仓储位置、出入库记录、库龄和质押/监管状态。"
    if any(kw in text for kw in ["利润", "盈利", "亏损"]):
        return "钢厂利润变化会影响采购节奏和付款能力，应关注上下游议价、账期拉长和回款稳定性。"
    if any(kw in text for kw in ["需求", "地产", "基建"]):
        return "需求端信息应转化为客户下游销售验证，重点看核心买方订单、发货验收和回款账户是否闭环。"
    return "该信息可作为行业背景和客户访谈线索，但新增业务判断仍应回到货权控制、回款闭环和交易真实性。"


def infer_verify_materials(text: str) -> str:
    base = ["购销合同", "发票/结算单", "出入库记录", "库存台账", "回款账户流水"]
    if any(kw in text for kw in ["库存", "仓库", "仓单"]):
        base.extend(["仓单", "监管仓协议", "库龄明细"])
    if any(kw in text for kw in ["需求", "订单", "交付"]):
        base.extend(["下游订单", "发货/验收记录"])
    if any(kw in text for kw in ["限产", "减产", "检修", "钢厂"]):
        base.extend(["钢厂通知", "采购计划", "交付排期"])
    return "、".join(dict.fromkeys(base))


def summarize_authority_context(news_data: Dict[str, Any]) -> str:
    items = collect_authority_items(news_data, limit=6)
    if not items:
        return "外部信息源暂未形成可引用的新增结论，业务判断主要依据本周价格、库存和PMI数据。"
    joined = " ".join(i.get("title", "") + " " + i.get("content", "") for i in items)
    has_policy = any(kw in joined for kw in ["限产", "减产", "检修", "产能", "政策", "零碳"])
    has_weak = any(kw in joined for kw in ["库存", "需求", "亏损", "利润", "盈利", "回款", "坏账"])
    if has_policy and has_weak:
        return "权威信息源共同指向一条主线：短期限产、减产和产业政策对钢价预期有一定支撑，但需求承接、库存去化和钢厂盈利压力尚未根本改善。"
    if has_policy:
        return "权威信息源显示，短期限产、减产或产业政策正在影响市场预期，但尚不能直接推导为客户融资安全边际改善。"
    if has_weak:
        return "权威信息源显示，行业需求、库存和盈利压力仍是本周主线，客户现金流和回款稳定性比单一价格涨跌更值得关注。"
    return "权威信息源未形成强方向性一致结论，本周仍应把外部信息作为客户访谈和材料核查线索。"


def build_authority_brief_section(news_data: Dict[str, Any]) -> str:
    items = collect_authority_items(news_data, limit=6)
    if not items:
        return "本周暂未抓取到可用于引用的 Wind 资讯。业务员仍需重点跟踪钢厂检修限产、库存去化、贸易商回款和核心买方付款节奏。\n"
    lines = ["以下简报只保留可转化为业务动作的信息：先说明外部信息的核心判断，再落到保理业务核查重点。\n"]
    for idx, item in enumerate(items, 1):
        title = item.get("title") or "未命名资讯"
        source = item.get("source") or "Wind资讯"
        item_date = item.get("date") or "日期未返回"
        content = (item.get("content") or "").strip()
        brief = content[:260] + ("..." if len(content) > 260 else "") if content else "接口未返回可展开摘要，需结合原文或后续人工复核。"
        url = item.get("url") or ""
        source_text = f"来源：{source}，{item_date}" + (f"，原文链接：{url}" if url else "")
        text = title + " " + content
        lines.append(f"### {idx}. {title}\n")
        lines.append(f"{source_text}。{brief}\n")
        lines.append(f"从业务含义看，{infer_authority_business_meaning(text)}本条信息对应的关键跟踪信号是：{extract_key_signal(text)}建议业务员重点核验：{infer_verify_materials(text)}。\n")
    return "\n".join(lines) + "\n"


def build_policy_and_mill_section(news_data: Dict[str, Any]) -> str:
    items = []
    for item in collect_authority_items(news_data, limit=12):
        text = item.get("title", "") + " " + item.get("content", "")
        if any(kw in text for kw in ["政策", "限产", "减产", "检修", "环保", "钢厂", "粗钢", "产量", "零碳"]):
            items.append(item)
    if not items:
        return "本周抓取信息中未识别到可直接引用的政策、限产或钢厂动态。下周仍建议关注粗钢压减、环保限产、钢厂检修和区域库存变化。\n"
    paragraphs = []
    for item in items[:4]:
        title = item.get("title") or "政策/钢厂动态"
        source = item.get("source") or "Wind资讯"
        item_date = item.get("date") or "日期未返回"
        url = item.get("url") or "Wind接口未返回公开链接"
        content = " ".join((item.get("content") or "").split())[:180]
        paragraphs.append(f"**{title}**（{source}，{item_date}，{url}）：{content if content else '接口未返回摘要。'}对业务端的使用方式不是简单判断价格涨跌，而是核实客户是否因政策或钢厂节奏变化出现采购提前、交付延期、账期拉长或回款不确定。")
    paragraphs.append("总体上，政策和钢厂动态只作为客户访谈和材料核查的触发器；若客户货权不可控、回款路径不清，即便行业信息偏利好，也不建议作为新增重点。")
    return "\n\n".join(paragraphs) + "\n"


def build_business_prompt(risks: List[Dict[str, str]], opportunities: List[Dict[str, str]]) -> str:
    parts = []
    if risks:
        parts.append("**风险提示**：" + "；".join(f"{r['message']}" for r in risks[:6]) + "。上述信号仅作为贷后和新增准入的筛查线索，最终应回到货权控制、回款闭环和客户实际周转验证。")
    if opportunities:
        parts.append("**机会提示**：" + "；".join(o["message"] for o in opportunities[:5]) + "。")
    return "\n\n".join(parts) + ("\n" if parts else "本周未触发显著风险或机会信号，建议维持常规跟踪。\n")


def build_business_opportunity_section(commodities: Dict[str, Any]) -> str:
    steel_names = ["螺纹钢", "盘螺", "线材", "H型钢"]
    raw_names = ["铁矿石", "焦炭", "废钢"]
    alloy_names = ["纯镍", "片钒", "铬铁", "锰硅", "钼铁", "黑钨精矿65"]
    steel_down = [n for n in steel_names if commodities.get(n, {}).get("direction") == "down"]
    steel_up = [n for n in steel_names if commodities.get(n, {}).get("direction") == "up"]
    raw_down = [n for n in raw_names if commodities.get(n, {}).get("direction") == "down"]
    raw_up = [n for n in raw_names if commodities.get(n, {}).get("direction") == "up"]
    alloy_volatile = [n for n in alloy_names if abs(commodities.get(n, {}).get("wow_pct") or 0) >= 1.5]
    paragraphs = [
        "本周机会判断以新增业务拓展为主，但准入口径保持偏保守：只有在货权可控、回款路径可闭环、交易背景能够穿透核验的前提下，价格波动才转化为可跟进机会；若货权、库存、回款任一环节无法闭合，则不建议仅因客户存在采购或补库需求而推进新增授信。"
    ]
    if steel_down or steel_up:
        if steel_down and steel_up:
            trend_text = f"成材端内部出现分化，{'、'.join(steel_up)}小幅上行，{'、'.join(steel_down)}回落"
        elif steel_down:
            trend_text = f"成材端以回落为主，{'、'.join(steel_down)}价格走弱"
        else:
            trend_text = f"成材端以小幅上行为主，{'、'.join(steel_up)}价格回升"
        paragraphs.append(f"**本周优先关注**的是周转快、库存轻、下游销售稳定的钢材贸易商。{trend_text}，整体并不支持高库存赌行情；对低库存、短账期、以销定采的客户，可围绕采购周转、库存周转或应收账款保理谨慎推进。")
    if raw_down or raw_up:
        raw_parts = []
        if raw_down:
            raw_parts.append(f"回落品种包括{'、'.join(raw_down)}")
        if raw_up:
            raw_parts.append(f"上行品种包括{'、'.join(raw_up)}")
        raw_trend = "原燃料端" + "，".join(raw_parts)
        paragraphs.append(f"**可谨慎跟进**的是有钢厂、加工厂或核心买方订单支撑的上游原燃料贸易商。{raw_trend}。核心判断不是客户说有采购需求，而是货物是否可识别、可监管、可处置，以及销售回款是否能够形成闭环。")
    paragraphs.append("**重点培育方向**是加工制造型客户。相较纯贸易客户，加工制造客户更容易形成采购、加工、交付、确权、回款的完整链条，也更适合从单纯货值判断转向订单和应收账款判断。")
    if alloy_volatile:
        paragraphs.append(f"**暂不建议重点拓展**的是价格弹性大、货权不清或锁价机制不足的合金类贸易客户。本周{'、'.join(alloy_volatile)}波动相对突出，若确需跟进，应限定在小额度、短账期、强货权监管、下游订单或回款闭环清楚的客户范围内。")
    paragraphs.append("综合来看，下一步业务员可优先筛选两类名单：一是低库存、快周转、仓储和回款可控的钢材贸易商；二是有稳定买方和可确权应收账款的加工制造客户。对高库存、慢周转、主要依赖行情判断获利、货权与回款路径不能闭环的客户，本周不建议作为新增业务重点。")
    return "\n\n".join(paragraphs) + "\n"


def build_report_markdown(report_date: str, processed: Dict[str, Any], charts: Dict[str, str], news_data: Optional[Dict[str, Any]] = None) -> str:
    commodities = processed["commodities"]
    sections = [f"# 朴晟科技钢铁产业链研究分析周报{report_date.replace('-', '')}\n", f"*报告日期：{report_date}*\n", "---\n"]
    sections.append("## 一、研究摘要\n")
    sections.append(build_core_summary(processed, news_data))
    sections.append(build_business_prompt(processed["risks"], processed["opportunities"]))
    sections.append("\n## 二、数据来源、口径与缺口声明\n")
    missing = [name for name in COMMODITIES if commodities.get(name, {}).get("latest_value") is None]
    sections.append("本报告价格数据由 Wind 金融终端获取，并统一按周度均价口径进行比较；日度数据按自然周聚合，周度和月度数据按原始频率纳入。库存、PMI 等供需指标作为辅助判断，不单独构成授信依据。\n")
    sections.append(f"片钒、铬铁等美元报价品种已按当日 USD/CNY 中间价 {processed.get('exchange_rate')} 折算为人民币口径展示；由于报价频率和口径与日度现货品种不同，环比仅作方向参考。\n")
    sections.append(("本期存在数据缺口：" + "、".join(missing) + "暂未取得有效价格序列。\n") if missing else "本期核心品种均取得有效价格序列。\n")
    sections.append("\n## 三、产业链总体运行情况\n")
    scissors = processed.get("scissors_diff", {})
    if len(scissors) >= 2:
        values = list(scissors.values())
        sections.append(f"本周原料成本与成材价格剪刀差{'扩大' if values[-1] > values[-2] else '收窄'}，最新值为{values[-1]:.2f}%，上周为{values[-2]:.2f}%。该指标仅反映价格传导趋势，不替代钢厂真实利润测算。\n")
    moved = sorted([(abs(v.get("wow_pct") or 0), name, v.get("wow_pct")) for name, v in commodities.items() if name in COMMODITIES and v.get("wow_pct") is not None], reverse=True)[:5]
    if moved:
        sections.append("从单品种变动看，本周波动较大的品种主要包括" + "、".join(f"{name}{pct:+.1f}%" for _, name, pct in moved) + "。\n")
    sections.append(build_price_table(commodities))
    if "scissors_diff" in charts:
        sections.append(f"\n![剪刀差趋势]({charts['scissors_diff']})\n")
    sections.append("\n---\n## 四、重点链条拆分分析\n")
    for idx, (group_key, title, names, note) in enumerate(GROUP_ORDER, 1):
        sections.append(f"\n### （{idx}）{title}\n")
        if f"overview_{group_key}" in charts:
            sections.append(f"![{title}价格对比]({charts[f'overview_{group_key}']})\n")
        sections.append(summarize_group(names, commodities) + "\n")
        for name in names:
            item = commodities.get(name, {})
            sections.append(f"\n#### {name}\n")
            if f"commodity_{name}" in charts:
                sections.append(f"![{name}走势]({charts[f'commodity_{name}']})\n")
            sections.append(trend_sentence(name, item) + "\n")
            sections.append(business_sentence(name, COMMODITIES[name]["group"]) + "\n")
        sections.append(f"\n从保理业务视角看，{note}\n")
    sections.append("\n---\n## 五、社会库存与供需研判\n")
    supply = []
    for name in INDICATORS:
        item = commodities.get(name, {})
        if item.get("latest_value") is not None:
            wow = item.get("wow_pct")
            supply.append(f"{name}最新值为{item['latest_value']:.2f}{item.get('unit','')}" + (f"，环比{wow:+.1f}%" if wow is not None else ""))
    sections.append(("；".join(supply) + "。库存与 PMI 指标主要用于验证需求强弱和贸易商周转压力。\n") if supply else "供需辅助指标本期暂未取得完整数据。\n")
    if "inventory" in charts:
        sections.append(f"\n![库存趋势]({charts['inventory']})\n")
    sections.append("\n---\n## 六、权威信息源简报\n")
    sections.append(build_authority_brief_section(news_data or {}))
    sections.append("\n---\n## 七、政策、限产与钢厂动态\n")
    sections.append(build_policy_and_mill_section(news_data or {}))
    sections.append("\n---\n## 八、本周保理业务机会与风险边界\n")
    sections.append(build_business_opportunity_section(commodities))
    sections.append("\n## 九、业务员核查清单\n")
    sections.append("| 核查主题 | 必看材料 | 判断要点 | 风险边界 |\n|---|---|---|---|\n| 货权控制 | 仓单、监管仓协议、出入库记录、库存台账、库龄明细 | 货物是否真实存在、是否可识别、可监管、可处置 | 货权不清或重复质押风险无法排除的客户，不作为新增重点 |\n| 回款闭环 | 下游合同、应收账款确权、指定回款账户流水、历史回款记录 | 回款是否进入可监控路径，账期是否与融资期限匹配 | 回款分散、依赖客户自述或账期明显拉长的客户，审慎或暂缓 |\n| 交易真实性 | 购销合同、发票、物流单、验收/签收记录 | 采购、发货、验收、结算链条是否能相互印证 | 单据链断裂、上下游关联异常、贸易背景弱的客户，暂缓 |\n| 库存与价格敞口 | 库存日报、跌价测试、锁价/套保安排 | 是否存在高库存赌行情，价格下行时保证金是否充足 | 高库存、慢周转、无锁价机制的客户，不建议新增 |\n")
    sections.append(f"\n---\n*免责声明：本报告数据来源于 Wind 金融终端及公开资讯，仅供内部研究参考，不构成投资建议。*\n\n*汇率：USD/CNY = {processed.get('exchange_rate')}（当日中间价，涉及美元报价品种已按该汇率折算为人民币口径展示）*\n")
    return "\n".join(sections)


def generate_steel_weekly_report(report_date: str, wind: WindClient, output_dir: Path, date_range: Optional[str] = None) -> Path:
    report_dir = output_dir / "steel-weekly" / report_date
    report_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = report_dir / "charts"
    if charts_dir.exists():
        shutil.rmtree(charts_dir)
    raw = fetch_raw_series(wind, date_range=date_range)
    exchange_rate = fetch_exchange_rate(wind)
    processed = process_raw_data(raw, exchange_rate)
    valid_count = sum(1 for name in COMMODITIES if processed["commodities"].get(name, {}).get("latest_value") is not None)
    if valid_count < max(8, len(COMMODITIES) // 2):
        sample_errors = [
            f"{name}: {series.error[:180]}"
            for name, series in raw.items()
            if name in COMMODITIES and series.error
        ][:3]
        detail = "；".join(sample_errors) if sample_errors else "Wind returned too few usable data series"
        raise RuntimeError(f"Wind data quality gate failed: {valid_count}/{len(COMMODITIES)} commodity series valid. {detail}")
    charts = generate_charts(processed, report_dir)
    news_data = fetch_wind_news(wind)
    report = build_report_markdown(report_date, processed, charts, news_data)
    path = report_dir / "report.md"
    path.write_text(report.strip() + "\n", encoding="utf-8")
    return path

"""Prompt templates."""
from __future__ import annotations

import json
from typing import Any, Dict


def build_steel_weekly_prompt(report_date: str, wind_data: Dict[str, Any]) -> str:
    data_json = json.dumps(wind_data, ensure_ascii=False, indent=2)[:30000]
    return f"""请基于以下 Wind 数据，生成《钢铁产业链研究周报》。

硬性要求：
1. 输出 Markdown。
2. 全文中文。
3. 只基于给定数据写作；数据不足时必须明确写“数据不足/接口未返回”，禁止编造。
4. 面向供应链金融/保理业务场景，不写投资建议。
5. 最终报告结构固定为：
   - 本周核心结论
   - 价格与供需观察
   - 对供应链金融业务的影响
   - 客户核查重点
   - 风险提示
   - 数据来源与免责声明

报告日期：{report_date}

Wind 数据：
```json
{data_json}
```
"""

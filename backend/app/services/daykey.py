"""日闭包口径（全局唯一架构：申报 dock_day 入湖）。

三条装载腿（预报/实收/回执）一律按请求体申报的 dock_day 落入正式表日闭包；
实收的 occurred_at 仅作审计留存，不参与折日，也不能撬动已封印闭包。
任何模块需要判断"此行属于哪一日"时只允许调用本模块，不得自行解析 occurred_at。
"""
from __future__ import annotations
import re
from datetime import datetime

# 全局口径标识，批次快照原样落库，供核对
DAY_KEY_RULE = "declared_dock_day"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class DayKeyError(ValueError):
    """申报日非法（空/格式错/日历日不存在）。"""

class SealedDayError(ValueError):
    """目标日闭包已封印，写入被闸拒绝。"""

def validate_dock_day(dock_day: str) -> str:
    if not isinstance(dock_day, str) or not DAY_RE.match(dock_day):
        raise DayKeyError("dock_day 格式应为 YYYY-MM-DD")
    try:
        datetime.strptime(dock_day, "%Y-%m-%d")
    except ValueError as e:
        raise DayKeyError(f"dock_day 不是合法日历日: {dock_day}") from e
    return dock_day

def validate_occurred_at(occurred_at: str | None) -> str:
    """occurred_at 只作审计字段：记录但不折日。合法 ISO8601 即可，缺省由调用方补当前时刻。"""
    if occurred_at is None:
        raise DayKeyError("occurred_at 缺失")
    try:
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as e:
        raise DayKeyError("occurred_at 不是合法 ISO8601 时间戳") from e
    return occurred_at

def resolve_day(dock_day: str, occurred_at: str | None = None) -> str:
    """返回行的正式落日闭包。

    架构 A：闭包键恒等于申报 dock_day；occurred_at 被显式忽略于折日逻辑，
    仅保留在调用方的审计列中。
    """
    return validate_dock_day(dock_day)

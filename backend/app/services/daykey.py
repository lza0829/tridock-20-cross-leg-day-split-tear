"""日闭包口径（全局唯一架构：首行锚日 first_complete_anchor_day）。

业务键 = (supplier_code, sku)。同一业务键三条装载腿（预报/实收/回执）必须收进
同一个日闭包：
- 该键第一笔完整落库的行，其申报 dock_day 成为锚日（anchor day），写入 day_anchors；
- 后续行无论申报哪一天，一律改挂（fold）到锚日，正式行 dock_day = 锚日，
  申报日原样留在 ingest_events.declared_dock_day 作为改挂痕迹，folded_count=1；
- 锚日闭包一旦封印，任何改挂都失败：正式表零写入，行进 quarantine_lines 隔离，
  快照记 quarantined_sealed，HTTP 非 200；
- 扫描物化、封印闸、看板、按日列表只允许按锚日（=正式表 dock_day）咬合。

本模块只负责日历日/时间戳的纯校验与口径常量；锚的解析与改挂见 services/anchor.py。
任何模块判断"此行属于哪一日"都不得自行解析 occurred_at 或做日期平移。
"""
from __future__ import annotations
import re
from datetime import datetime

# 全局口径标识：首行锚日。批次/快照原样落库，供核对
DAY_KEY_RULE = "first_complete_anchor_day"
# 兼容旧快照里的历史口径标识，仅用于识别，绝不允许再按它折日
LEGACY_RULE = "declared_dock_day"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class DayKeyError(ValueError):
    """申报日非法（空/格式错/日历日不存在）。"""

class SealedDayError(ValueError):
    """目标日闭包已封印，写入被闸拒绝（新键首行落在封印日）。"""

class AnchorQuarantineError(ValueError):
    """业务键的锚日已封印，改挂失败，行已进隔离区（非 200，正式表零写入）。"""

def validate_dock_day(dock_day: str) -> str:
    if not isinstance(dock_day, str) or not DAY_RE.match(dock_day):
        raise DayKeyError("dock_day 格式应为 YYYY-MM-DD")
    try:
        datetime.strptime(dock_day, "%Y-%m-%d")
    except ValueError as e:
        raise DayKeyError(f"dock_day 不是合法日历日: {dock_day}") from e
    return dock_day

def validate_occurred_at(occurred_at: str | None) -> str:
    """occurred_at 只作审计字段：记录但绝不参与折日/定锚。合法 ISO8601 即可。"""
    if occurred_at is None:
        raise DayKeyError("occurred_at 缺失")
    try:
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as e:
        raise DayKeyError("occurred_at 不是合法 ISO8601 时间戳") from e
    return occurred_at

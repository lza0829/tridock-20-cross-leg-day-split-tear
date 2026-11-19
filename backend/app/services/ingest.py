"""批次快照（装载事件湖）。

三腿每一笔装载尝试都在与正式表写入同一个 BEGIN IMMEDIATE 事务内落一条 ingest_events：
- accepted：正式行与快照同事务提交，不存在"快照成功/正式表半截"；
- rejected_sealed：封印闸拒绝时同事务只落快照（随后整体提交，正式表零写入）。
快照固定记录 fold_rule=declared_dock_day、申报日、实收 occurred_at（仅审计）与折日笔数，
架构 A 下折日笔数恒为 0——落日闭包永不来自 occurred_at。
"""
from __future__ import annotations
import sqlite3

from app.services.daykey import DAY_KEY_RULE

LEGS = ("expect", "receive", "confirm")

def record_event(
    conn: sqlite3.Connection,
    *,
    leg: str,
    line_no: str,
    supplier_code: str,
    sku: str,
    declared_dock_day: str,
    qty: float,
    occurred_at: str | None,
    result: str,
    reason: str | None = None,
) -> int:
    if leg not in LEGS:
        raise ValueError(f"非法装载腿: {leg}")
    if result not in ("accepted", "rejected_sealed"):
        raise ValueError(f"非法装载结果: {result}")
    cur = conn.execute(
        '''INSERT INTO ingest_events(
               leg, line_no, supplier_code, sku,
               declared_dock_day, closed_dock_day, occurred_at, qty,
               fold_rule, folded_count, result, reason, created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            leg,
            line_no,
            supplier_code,
            sku,
            declared_dock_day,
            declared_dock_day,  # 架构 A：闭包日恒等于申报日
            occurred_at,
            qty,
            DAY_KEY_RULE,
            0,  # 架构 A 无折日
            result,
            reason,
            _now(),
        ),
    )
    return int(cur.lastrowid)

def list_events(
    conn: sqlite3.Connection,
    *,
    dock_day: str | None = None,
    leg: str | None = None,
    result: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM ingest_events WHERE 1=1"
    args: list = []
    if dock_day:
        sql += " AND closed_dock_day=?"
        args.append(dock_day)
    if leg:
        sql += " AND leg=?"
        args.append(leg)
    if result:
        sql += " AND result=?"
        args.append(result)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()

def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

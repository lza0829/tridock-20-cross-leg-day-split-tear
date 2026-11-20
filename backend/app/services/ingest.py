"""批次快照（装载事件湖，全局唯一架构：首行锚日）。

三腿每一笔装载尝试都在与正式表/锚表/隔离区写入同一个 BEGIN IMMEDIATE 事务内落
一条 ingest_events，终态三选一，互不存在半截：
- accepted：正式行（挂锚日）与快照同事务提交；申报日≠锚日时 folded_count=1，
  reason='改挂至锚日'，declared_dock_day 原样保留改挂痕迹；
- quarantined_sealed：锚日已封印，改挂失败。正式表零写入，行落 quarantine_lines，
  快照闭包日记锚日、folded_count 按是否跨日记录；
- rejected_sealed：业务键尚无锚且申报日已封印，禁止在封印日立锚。正式表零写入。

快照固定 fold_rule=first_complete_anchor_day；历史旧快照可能带 declared_dock_day，
只作只读审计，不参与任何折日。
"""
from __future__ import annotations
import sqlite3

from app.services.daykey import DAY_KEY_RULE

LEGS = ("expect", "receive", "confirm")
RESULTS = ("accepted", "rejected_sealed", "quarantined_sealed")

def record_event(
    conn: sqlite3.Connection,
    *,
    leg: str,
    line_no: str,
    supplier_code: str,
    sku: str,
    declared_dock_day: str,
    closed_dock_day: str,
    qty: float,
    occurred_at: str | None,
    result: str,
    folded_count: int = 0,
    reason: str | None = None,
) -> int:
    if leg not in LEGS:
        raise ValueError(f"非法装载腿: {leg}")
    if result not in RESULTS:
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
            closed_dock_day,
            occurred_at,
            qty,
            DAY_KEY_RULE,
            folded_count,
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
        # 快照按闭包日（锚日）过滤，与扫描/看板同标签
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

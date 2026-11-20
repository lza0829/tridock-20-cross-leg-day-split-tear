"""锚日解析、改挂与隔离（全局唯一架构：首行锚日 first_complete_anchor_day）。

业务键 (supplier_code, sku) 的锚日持久化在 day_anchors，由该键第一笔完整落库的
行在同一写事务内创建，之后永不改变（锚日封印后也不允许换锚，只能隔离）。

装载事务内的判定顺序（全部在 BEGIN IMMEDIATE 之后，与结案事务互斥）：
1. 业务键已有锚：
   a. 锚日已封印 → 改挂失败：正式表零写入，行落 quarantine_lines、快照记
      quarantined_sealed，提交后抛 AnchorQuarantineError（HTTP 409，留痕不撒谎）；
   b. 锚日开放 → 正式行 dock_day 改挂为锚日；申报日≠锚日时 folded_count=1。
2. 业务键无锚：
   a. 申报日已封印 → 不能在封印闭包上立锚：快照 rejected_sealed、正式表零写入；
   b. 申报日开放 → 以申报日立锚并写入，该行即"最先完整落库的一行"。

任何失败路径要么提交留痕（隔离/拒绝），要么整体回滚（唯一键冲突等），
不存在正式表半截行或"快照 accepted 但正式行缺失"。
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

from app.services import ingest
from app.services.gate import is_sealed
from app.services.daykey import (
    DAY_KEY_RULE,
    AnchorQuarantineError,
    SealedDayError,
    validate_dock_day,
)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def get_anchor(conn: sqlite3.Connection, supplier_code: str, sku: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM day_anchors WHERE supplier_code=? AND sku=? LIMIT 1",
        (supplier_code, sku),
    ).fetchone()

def create_anchor(
    conn: sqlite3.Connection, *, supplier_code: str, sku: str,
    anchor_day: str, anchor_leg: str, anchor_line_no: str,
) -> None:
    conn.execute(
        '''INSERT INTO day_anchors(supplier_code, sku, anchor_day, anchor_leg, anchor_line_no, created_at)
           VALUES (?,?,?,?,?,?)''',
        (supplier_code, sku, anchor_day, anchor_leg, anchor_line_no, _now()),
    )

def quarantine_line(
    conn: sqlite3.Connection, *, leg: str, line_no: str, supplier_code: str, sku: str,
    declared_dock_day: str, anchor_day: str, qty: float,
    occurred_at: str | None, reason: str,
) -> None:
    conn.execute(
        '''INSERT INTO quarantine_lines(
               leg, line_no, supplier_code, sku, declared_dock_day, anchor_day,
               qty, occurred_at, reason, created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (leg, line_no, supplier_code, sku, declared_dock_day, anchor_day,
         qty, occurred_at, reason, _now()),
    )

def list_quarantine(
    conn: sqlite3.Connection, *, anchor_day: str | None = None, limit: int = 500,
) -> list[sqlite3.Row]:
    if anchor_day:
        return conn.execute(
            "SELECT * FROM quarantine_lines WHERE anchor_day=? ORDER BY id DESC LIMIT ?",
            (anchor_day, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM quarantine_lines ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

def ingest_leg(
    conn: sqlite3.Connection,
    *,
    leg: str,
    line_no: str,
    supplier_code: str,
    sku: str,
    declared_dock_day: str,
    qty: float,
    occurred_at: str | None,
    build_insert,
) -> dict:
    """三腿统一装载事务。build_insert(closed_day) 返回 (sql, args)，正式行一律挂锚日。"""
    day = validate_dock_day(declared_dock_day)
    conn.execute("BEGIN IMMEDIATE")
    committed = False
    try:
        anchor = get_anchor(conn, supplier_code, sku)
        if anchor is not None:
            anchor_day = anchor["anchor_day"]
            if is_sealed(conn, anchor_day):
                reason = f"业务键({supplier_code},{sku})锚日 {anchor_day} 已封印，改挂失败，行隔离"
                quarantine_line(
                    conn, leg=leg, line_no=line_no, supplier_code=supplier_code, sku=sku,
                    declared_dock_day=day, anchor_day=anchor_day, qty=qty,
                    occurred_at=occurred_at, reason=reason,
                )
                ingest.record_event(
                    conn, leg=leg, line_no=line_no, supplier_code=supplier_code, sku=sku,
                    declared_dock_day=day, closed_dock_day=anchor_day, qty=qty,
                    occurred_at=occurred_at, result="quarantined_sealed",
                    folded_count=1 if anchor_day != day else 0, reason=reason,
                )
                conn.commit()
                committed = True
                raise AnchorQuarantineError(reason)
            closed_day = anchor_day
            folded = anchor_day != day
        else:
            if is_sealed(conn, day):
                reason = f"申报日 {day} 已封印，且业务键({supplier_code},{sku})尚无锚，禁止在封印日立锚"
                ingest.record_event(
                    conn, leg=leg, line_no=line_no, supplier_code=supplier_code, sku=sku,
                    declared_dock_day=day, closed_dock_day=day, qty=qty,
                    occurred_at=occurred_at, result="rejected_sealed",
                    folded_count=0, reason=reason,
                )
                conn.commit()
                committed = True
                raise SealedDayError(reason)
            create_anchor(
                conn, supplier_code=supplier_code, sku=sku,
                anchor_day=day, anchor_leg=leg, anchor_line_no=line_no,
            )
            closed_day = day
            folded = False

        sql, args = build_insert(closed_day)
        conn.execute(sql, args)
        ingest.record_event(
            conn, leg=leg, line_no=line_no, supplier_code=supplier_code, sku=sku,
            declared_dock_day=day, closed_dock_day=closed_day, qty=qty,
            occurred_at=occurred_at, result="accepted",
            folded_count=1 if folded else 0,
            reason=("改挂至锚日" if folded else None),
        )
        conn.commit()
        committed = True
        return {
            "ok": True,
            "dock_day": closed_day,
            "declared_dock_day": day,
            "anchor_day": closed_day,
            "folded": folded,
            "fold_rule": DAY_KEY_RULE,
        }
    except Exception:
        if not committed:
            conn.rollback()
        raise

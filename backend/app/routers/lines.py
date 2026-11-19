from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import connect
from app.services.gate import assert_writable
from app.services import ingest
from app.services import leg_day
from app.services.daykey import (
    DayKeyError,
    SealedDayError,
    validate_dock_day,
    validate_occurred_at,
)

router = APIRouter(tags=["lines"])

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class ExpectIn(BaseModel):
    doc_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    note: str | None = None

class ReceiveIn(BaseModel):
    receive_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    occurred_at: str | None = None
    note: str | None = None

class ConfirmIn(BaseModel):
    confirm_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    note: str | None = None

def _ingest(conn, *, leg, line_no, supplier_code, sku, dock_day, qty, occurred_at, insert_sql):
    """三腿统一装载事务。

    架构 A 口径在此强制落地：
    1. 申报 dock_day 先过格式/日历日校验，它就是正式表与快照的唯一闭包键；
    2. BEGIN IMMEDIATE 拿写锁后在事务内复查封印——并发结案/并发装载在此串行，
       已封印日不可能被任何一腿旁路撬写，也不会出现正式表半截写入；
    3. 正式行与 accepted 快照同事务提交；封印拒绝只留 rejected_sealed 快照。
    """
    day = validate_dock_day(dock_day)
    conn.execute("BEGIN IMMEDIATE")
    committed = False
    try:
        try:
            assert_writable(conn, day)
        except SealedDayError as exc:
            ingest.record_event(
                conn,
                leg=leg,
                line_no=line_no,
                supplier_code=supplier_code,
                sku=sku,
                declared_dock_day=day,
                qty=qty,
                occurred_at=occurred_at,
                result="rejected_sealed",
                reason=str(exc),
            )
            conn.commit()
            committed = True
            raise
        conn.execute(insert_sql["sql"], insert_sql["args"])
        ingest.record_event(
            conn,
            leg=leg,
            line_no=line_no,
            supplier_code=supplier_code,
            sku=sku,
            declared_dock_day=day,
            qty=qty,
            occurred_at=occurred_at,
            result="accepted",
        )
        conn.commit()
        committed = True
    except Exception:
        if not committed:
            conn.rollback()
        raise

@router.get("/suppliers")
def list_suppliers():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM suppliers ORDER BY code").fetchall()]
    finally:
        conn.close()

@router.get("/expects")
def list_expects(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM expect_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM expect_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/expects")
def add_expect(body: ExpectIn):
    conn = connect()
    try:
        _ingest(
            conn,
            leg="expect",
            line_no=body.doc_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            dock_day=body.dock_day,
            qty=body.qty,
            occurred_at=None,
            insert_sql={
                "sql": '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
                          VALUES (?,?,?,?,?,?,?)''',
                "args": (body.doc_no, body.supplier_code, body.sku,
                         validate_dock_day(body.dock_day), body.qty, body.note, _now()),
            },
        )
        return {"ok": True, "dock_day": validate_dock_day(body.dock_day), "fold_rule": "declared_dock_day"}
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"写入失败: {e}") from e
    finally:
        conn.close()

@router.get("/receives")
def list_receives(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM receive_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM receive_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/receives")
def add_receive(body: ReceiveIn):
    conn = connect()
    try:
        # occurred_at 仅审计：先校验合法性，但绝不参与落日闭包计算
        occurred = validate_occurred_at(body.occurred_at) if body.occurred_at else _now()
        day = leg_day.receive_day(body.dock_day)
        _ingest(
            conn,
            leg="receive",
            line_no=body.receive_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            dock_day=day,
            qty=body.qty,
            occurred_at=occurred,
            insert_sql={
                "sql": '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
                          VALUES (?,?,?,?,?,?,?,?)''',
                "args": (body.receive_no, body.supplier_code, body.sku,
                         day, body.qty, occurred, body.note, _now()),
            },
        )
        return {
            "ok": True,
            "dock_day": day,
            "declared_dock_day": day,
            "occurred_at": occurred,
            "fold_rule": "declared_dock_day",
            "folded": False,
        }
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"写入失败: {e}") from e
    finally:
        conn.close()

@router.get("/confirms")
def list_confirms(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM confirm_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM confirm_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/confirms")
def add_confirm(body: ConfirmIn):
    conn = connect()
    try:
        _ingest(
            conn,
            leg="confirm",
            line_no=body.confirm_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            dock_day=body.dock_day,
            qty=body.qty,
            occurred_at=None,
            insert_sql={
                "sql": '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
                          VALUES (?,?,?,?,?,?,?)''',
                "args": (body.confirm_no, body.supplier_code, body.sku,
                         validate_dock_day(body.dock_day), body.qty, body.note, _now()),
            },
        )
        return {"ok": True, "dock_day": validate_dock_day(body.dock_day), "fold_rule": "declared_dock_day"}
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"写入失败: {e}") from e
    finally:
        conn.close()

@router.get("/ingest-events")
def list_ingest_events(dock_day: str | None = None, leg: str | None = None,
                       result: str | None = None, limit: int = 200):
    """批次快照查询：按闭包日/腿/结果核对装载口径。"""
    conn = connect()
    try:
        rows = ingest.list_events(
            conn, dock_day=dock_day, leg=leg, result=result, limit=min(limit, 1000)
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()

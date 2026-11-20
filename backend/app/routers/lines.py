from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import connect
from app.services import ingest
from app.services import anchor
from app.services.daykey import (
    DAY_KEY_RULE,
    AnchorQuarantineError,
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
        out = anchor.ingest_leg(
            conn,
            leg="expect",
            line_no=body.doc_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            declared_dock_day=body.dock_day,
            qty=body.qty,
            occurred_at=None,
            build_insert=lambda closed_day: (
                '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (body.doc_no, body.supplier_code, body.sku,
                 closed_day, body.qty, body.note, _now()),
            ),
        )
        return out
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except AnchorQuarantineError as e:
        # 改挂失败已进隔离区：非 200，但行有隔离+快照双重痕迹，正式表零写入
        raise HTTPException(409, str(e)) from e
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
        # occurred_at 仅审计：先校验合法性，但绝不参与定锚/折日
        occurred = validate_occurred_at(body.occurred_at) if body.occurred_at else _now()
        out = anchor.ingest_leg(
            conn,
            leg="receive",
            line_no=body.receive_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            declared_dock_day=body.dock_day,
            qty=body.qty,
            occurred_at=occurred,
            build_insert=lambda closed_day: (
                '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (body.receive_no, body.supplier_code, body.sku,
                 closed_day, body.qty, occurred, body.note, _now()),
            ),
        )
        out["occurred_at"] = occurred
        return out
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except AnchorQuarantineError as e:
        raise HTTPException(409, str(e)) from e
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
        out = anchor.ingest_leg(
            conn,
            leg="confirm",
            line_no=body.confirm_no,
            supplier_code=body.supplier_code,
            sku=body.sku,
            declared_dock_day=body.dock_day,
            qty=body.qty,
            occurred_at=None,
            build_insert=lambda closed_day: (
                '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (body.confirm_no, body.supplier_code, body.sku,
                 closed_day, body.qty, body.note, _now()),
            ),
        )
        return out
    except (DayKeyError, SealedDayError) as e:
        raise HTTPException(400, str(e)) from e
    except AnchorQuarantineError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"写入失败: {e}") from e
    finally:
        conn.close()

@router.get("/ingest-events")
def list_ingest_events(dock_day: str | None = None, leg: str | None = None,
                       result: str | None = None, limit: int = 200):
    """批次快照查询：按锚日/腿/结果核对装载口径与改挂痕迹。"""
    conn = connect()
    try:
        rows = ingest.list_events(
            conn, dock_day=dock_day, leg=leg, result=result, limit=min(limit, 1000)
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.get("/quarantine")
def list_quarantine(anchor_day: str | None = None):
    """隔离区：锚日封死后改挂失败、未能进任何正式闭包的窗外半截行。"""
    conn = connect()
    try:
        return [dict(r) for r in anchor.list_quarantine(conn, anchor_day=anchor_day)]
    finally:
        conn.close()

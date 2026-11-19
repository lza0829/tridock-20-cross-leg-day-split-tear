from app.services.day_split import board_day
from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.gate import is_sealed

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day = conn.execute("SELECT value FROM settings WHERE key='dock_day'").fetchone()["value"]
        # 日计数统一以正式表 dock_day 闭包为准（架构 A：申报日即落日，occurred_at 不折日），
        # 与按日列表、对齐物化同列同源，杜绝总览/列表各折各的日
        expect_n = conn.execute("SELECT COUNT(*) c FROM expect_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        receive_n = conn.execute("SELECT COUNT(*) c FROM receive_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        confirm_n = conn.execute("SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        accepted_ingest = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND result='accepted'",
            (day,),
        ).fetchone()["c"]
        rejected_ingest = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND result='rejected_sealed'",
            (day,),
        ).fetchone()["c"]
        batch = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        pending = 0
        if batch:
            pending = conn.execute(
                "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND fate='pending'",
                (batch["id"],),
            ).fetchone()["c"]
        return {
            "dock_day": day,
            "fold_rule": "declared_dock_day",
            "sealed": is_sealed(conn, day),
            "expect_count": expect_n,
            "receive_count": receive_n,
            "confirm_count": confirm_n,
            "ingest_accepted_count": accepted_ingest,
            "ingest_rejected_sealed_count": rejected_ingest,
            "latest_batch": dict(batch) if batch else None,
            "pending_variance": pending,
        }
    finally:
        conn.close()

"""总览看板（全局唯一架构：首行锚日）。

看板卡片、扫描窗、闸位图共用同一个日标签——settings.dock_day 即锚日，
所有计数只认正式表 dock_day 闭包与同标签快照，杜绝卡片在别的日子加一。
"""
from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.daykey import DAY_KEY_RULE
from app.services.gate import is_sealed

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day = conn.execute("SELECT value FROM settings WHERE key='dock_day'").fetchone()["value"]
        # 日计数统一以正式表 dock_day 锚日闭包为准，与按日列表、对齐物化同列同源
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
        quarantined_ingest = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND result='quarantined_sealed'",
            (day,),
        ).fetchone()["c"]
        folded_ingest = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND folded_count>0",
            (day,),
        ).fetchone()["c"]
        quarantine_n = conn.execute(
            "SELECT COUNT(*) c FROM quarantine_lines WHERE anchor_day=?", (day,)
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
        # 看板自证：正式行总数必须等于 accepted 快照数，否则就是双计/缺计
        formal_total = expect_n + receive_n + confirm_n
        return {
            "dock_day": day,
            "fold_rule": DAY_KEY_RULE,
            "sealed": is_sealed(conn, day),
            "expect_count": expect_n,
            "receive_count": receive_n,
            "confirm_count": confirm_n,
            "formal_total": formal_total,
            "ingest_accepted_count": accepted_ingest,
            "ingest_rejected_sealed_count": rejected_ingest,
            "ingest_quarantined_count": quarantined_ingest,
            "ingest_folded_count": folded_ingest,
            "quarantine_count": quarantine_n,
            "counts_consistent": formal_total == accepted_ingest,
            "latest_batch": dict(batch) if batch else None,
            "pending_variance": pending,
        }
    finally:
        conn.close()

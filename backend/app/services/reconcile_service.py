"""三联对账与日闭包封印（全局唯一架构：首行锚日）。

扫描物化在 BEGIN IMMEDIATE 事务内完成，与装载/改挂事务互斥。物化前强制做
锚日完整性校验，任一处撒谎即整体回滚、不产出批次：
1. 当日三张正式表里每个业务键都能在 day_anchors 找到锚，且锚日就是当日
   （跨日同键不可能进同一匹配桶）；
2. 当日正式行总数 == ingest_events 当日 accepted 快照数
   （结案日键集合必须等于扫描窗实际吞下的行，杜绝双计/缺计）；
3. 隔离区行不进任何日的物化。
"""
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3

from app.services.day_split import scan_day
from app.services.daykey import DAY_KEY_RULE, validate_dock_day

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _sum_map(conn: sqlite3.Connection, table: str, dock_day: str) -> dict[tuple[str, str], float]:
    # 对齐扫描只认正式表 dock_day 闭包（=锚日），不读 occurred_at、不做日期平移
    rows = conn.execute(
        f"SELECT supplier_code, sku, SUM(qty) q FROM {table} WHERE dock_day=? GROUP BY supplier_code, sku",
        (dock_day,),
    ).fetchall()
    return {(r["supplier_code"], r["sku"]): float(r["q"]) for r in rows}

def _formal_row_count(conn: sqlite3.Connection, dock_day: str) -> int:
    n = 0
    for table in ("expect_lines", "receive_lines", "confirm_lines"):
        n += conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE dock_day=?", (dock_day,)
        ).fetchone()["c"]
    return n

def verify_day_integrity(conn: sqlite3.Connection, day: str) -> dict:
    """日闸与三行键集合的真相校验。返回计数摘要；发现撕裂立即抛错，事务回滚。"""
    anchor_map = {
        (r["supplier_code"], r["sku"]): r["anchor_day"]
        for r in conn.execute("SELECT supplier_code, sku, anchor_day FROM day_anchors").fetchall()
    }
    formal_keys: set[tuple[str, str]] = set()
    for table in ("expect_lines", "receive_lines", "confirm_lines"):
        for r in conn.execute(
            f"SELECT DISTINCT supplier_code, sku FROM {table} WHERE dock_day=?", (day,)
        ).fetchall():
            key = (r["supplier_code"], r["sku"])
            formal_keys.add(key)
            anchored = anchor_map.get(key)
            if anchored is None:
                raise ValueError(f"完整性校验失败：{table} 键 {key} 在 {day} 无锚日记录")
            if anchored != day:
                raise ValueError(
                    f"完整性校验失败：键 {key} 正式行挂 {day}，锚日却是 {anchored}（跨日捏合）"
                )
    # 同一业务键绝不允许在两个日历日各出现一次（两日双计的库级防线）
    for key in formal_keys:
        seen_days = set()
        for table in ("expect_lines", "receive_lines", "confirm_lines"):
            for r in conn.execute(
                f"SELECT DISTINCT dock_day FROM {table} WHERE supplier_code=? AND sku=?",
                key,
            ).fetchall():
                seen_days.add(r["dock_day"])
        if seen_days - {day}:
            raise ValueError(f"完整性校验失败：键 {key} 跨日出现于 {sorted(seen_days)}")

    formal_n = _formal_row_count(conn, day)
    accepted_n = conn.execute(
        "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND result='accepted'",
        (day,),
    ).fetchone()["c"]
    if formal_n != accepted_n:
        raise ValueError(
            f"完整性校验失败：{day} 正式行 {formal_n} 行 vs accepted 快照 {accepted_n} 条，键集合撒谎"
        )
    return {"formal_rows": formal_n, "accepted_events": accepted_n, "keys": len(formal_keys)}

def run_reconcile(conn: sqlite3.Connection, dock_day: str) -> dict:
    dock_day = scan_day(dock_day)
    day = validate_dock_day(dock_day)
    # 与装载事务互斥：扫描物化期间三腿表/锚表冻结
    conn.execute("BEGIN IMMEDIATE")
    try:
        sealed = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='sealed'",
            (day,),
        ).fetchone()
        if sealed:
            raise ValueError(f"锚日 {day} 已结案，不能重跑对账")

        integrity = verify_day_integrity(conn, day)

        # drop previous open batch for same day
        old = conn.execute(
            "SELECT id FROM reconcile_batches WHERE dock_day=? AND status='open'",
            (day,),
        ).fetchall()
        for r in old:
            conn.execute("DELETE FROM match_rows WHERE batch_id=?", (r["id"],))
            conn.execute("DELETE FROM variance_rows WHERE batch_id=?", (r["id"],))
            conn.execute("DELETE FROM reconcile_batches WHERE id=?", (r["id"],))

        expect_m = _sum_map(conn, "expect_lines", day)
        receive_m = _sum_map(conn, "receive_lines", day)
        confirm_m = _sum_map(conn, "confirm_lines", day)
        keys = set(expect_m) | set(receive_m) | set(confirm_m)

        n = conn.execute("SELECT COUNT(*) c FROM reconcile_batches").fetchone()["c"] + 1
        batch_no = f"RB-{day.replace('-', '')}-{n:03d}"
        cur = conn.execute(
            '''INSERT INTO reconcile_batches(batch_no, dock_day, matched_count, variance_count,
                   status, sealed_at, note, fold_rule)
               VALUES (?,?,0,0,'open',NULL,?,?)''',
            (batch_no, day, "三联对齐/首行锚日闭包", DAY_KEY_RULE),
        )
        batch_id = int(cur.lastrowid)

        matched = 0
        variance = 0
        now = _now()
        for supplier_code, sku in sorted(keys):
            eq = expect_m.get((supplier_code, sku), 0.0)
            rq = receive_m.get((supplier_code, sku), 0.0)
            cq = confirm_m.get((supplier_code, sku), 0.0)
            aligned = 1 if eq == rq == cq else 0
            conn.execute(
                '''INSERT INTO match_rows(batch_id, dock_day, supplier_code, sku, expect_qty, receive_qty, confirm_qty, aligned)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (batch_id, day, supplier_code, sku, eq, rq, cq, aligned),
            )
            if aligned:
                matched += 1
            else:
                variance += 1
                conn.execute(
                    '''INSERT INTO variance_rows(batch_id, dock_day, supplier_code, sku, expect_qty, receive_qty, confirm_qty, fate, created_at)
                       VALUES (?,?,?,?,?,?,?,'pending',?)''',
                    (batch_id, day, supplier_code, sku, eq, rq, cq, now),
                )

        # 物化后复查：批次键集合必须等于当日三行键并集
        materialized = {
            (r["supplier_code"], r["sku"])
            for r in conn.execute(
                "SELECT supplier_code, sku FROM match_rows WHERE batch_id=?", (batch_id,)
            ).fetchall()
        }
        if materialized != keys:
            raise ValueError(
                f"完整性校验失败：物化键集合 {sorted(materialized)} != 三行并集 {sorted(keys)}"
            )

        conn.execute(
            "UPDATE reconcile_batches SET matched_count=?, variance_count=? WHERE id=?",
            (matched, variance, batch_id),
        )
        conn.commit()
        return {
            "batch_id": batch_id,
            "batch_no": batch_no,
            "dock_day": day,
            "matched_count": matched,
            "variance_count": variance,
            "status": "open",
            "fold_rule": DAY_KEY_RULE,
            "integrity": integrity,
        }
    except Exception:
        conn.rollback()
        raise

def seal_day(conn: sqlite3.Connection, dock_day: str) -> dict:
    day = validate_dock_day(dock_day)
    # 封印在写事务内完成：事务提交前任何装载事务都拿不到写锁，
    # 提交后封印闸立即生效，不存在"封印与写入交错"窗口；封印前再跑一次真相校验
    conn.execute("BEGIN IMMEDIATE")
    try:
        verify_day_integrity(conn, day)
        batch = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='open' ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        if not batch:
            raise ValueError("没有可结案的对账批次，请先跑三联对账")
        pending = conn.execute(
            "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND fate='pending'",
            (batch["id"],),
        ).fetchone()["c"]
        if pending:
            raise ValueError(f"仍有 {pending} 条差额未处置，不能结案")
        now = _now()
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed', sealed_at=? WHERE id=?",
            (now, batch["id"],),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (batch["id"],)).fetchone())
    except Exception:
        conn.rollback()
        raise

def set_variance_fate(conn: sqlite3.Connection, variance_id: int, fate: str) -> dict:
    allowed = {"pending", "accept_short", "accept_over", "quarantine"}
    if fate not in allowed:
        raise ValueError("非法差额命运")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone()
        if not row:
            raise ValueError("差额行不存在")
        batch = conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (row["batch_id"],)).fetchone()
        if batch and batch["status"] == "sealed":
            raise ValueError("已结案批次不可改差额命运")
        conn.execute("UPDATE variance_rows SET fate=? WHERE id=?", (fate, variance_id))
        conn.commit()
        return dict(conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise

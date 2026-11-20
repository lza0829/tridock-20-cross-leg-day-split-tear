"""封印闸（全局唯一架构：首行锚日）。

闸位图与扫描窗咬同一个日标签：一个日闭包封印 = reconcile_batches 中该 dock_day
存在 status='sealed' 的批次。封印后的闭包拒绝任何新写入与任何改挂。
"""
from __future__ import annotations
import sqlite3

from app.services.day_split import gate_day

def sealed_batch(conn: sqlite3.Connection, dock_day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='sealed' LIMIT 1",
        (gate_day(dock_day),),
    ).fetchone()

def is_sealed(conn: sqlite3.Connection, dock_day: str) -> bool:
    return sealed_batch(conn, dock_day) is not None

def assert_writable(conn: sqlite3.Connection, dock_day: str) -> None:
    if is_sealed(conn, dock_day):
        raise ValueError(f"锚日 {dock_day} 已结案封印，禁止再写入/改挂")

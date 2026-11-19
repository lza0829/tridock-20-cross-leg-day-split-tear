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
        raise ValueError(f"到货日 {dock_day} 已结案，禁止再改预报/实收/回执")

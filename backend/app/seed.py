from __future__ import annotations
from app.db import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expect_lines (
  id INTEGER PRIMARY KEY,
  doc_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receive_lines (
  id INTEGER PRIMARY KEY,
  receive_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirm_lines (
  id INTEGER PRIMARY KEY,
  confirm_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL UNIQUE,
  dock_day TEXT NOT NULL,
  matched_count INTEGER NOT NULL,
  variance_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  sealed_at TEXT,
  note TEXT,
  fold_rule TEXT NOT NULL DEFAULT 'declared_dock_day'
);
CREATE TABLE IF NOT EXISTS match_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  aligned INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  fate TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS late_spill (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none'
);
CREATE TABLE IF NOT EXISTS ingest_events (
  id INTEGER PRIMARY KEY,
  leg TEXT NOT NULL,
  line_no TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  declared_dock_day TEXT NOT NULL,
  closed_dock_day TEXT NOT NULL,
  occurred_at TEXT,
  qty REAL NOT NULL,
  fold_rule TEXT NOT NULL,
  folded_count INTEGER NOT NULL,
  result TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_closed_day ON ingest_events(closed_dock_day);
CREATE INDEX IF NOT EXISTS idx_ingest_result ON ingest_events(result);
'''

# 已存在库的轻量迁移：补齐快照表（旧库无此表时 SCHEMA 的 IF NOT EXISTS 已覆盖，
# 这里保留显式迁移点，后续口径升级只在此追加）。
MIGRATIONS = [
    '''CREATE TABLE IF NOT EXISTS ingest_events (
  id INTEGER PRIMARY KEY,
  leg TEXT NOT NULL,
  line_no TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  declared_dock_day TEXT NOT NULL,
  closed_dock_day TEXT NOT NULL,
  occurred_at TEXT,
  qty REAL NOT NULL,
  fold_rule TEXT NOT NULL,
  folded_count INTEGER NOT NULL,
  result TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
)''',
    "CREATE INDEX IF NOT EXISTS idx_ingest_closed_day ON ingest_events(closed_dock_day)",
    "CREATE INDEX IF NOT EXISTS idx_ingest_result ON ingest_events(result)",
    # 旧库补批次口径列；全局架构 A，历史批次同口径
    "ALTER TABLE reconcile_batches ADD COLUMN fold_rule TEXT NOT NULL DEFAULT 'declared_dock_day'",
]

def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        existing_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(reconcile_batches)").fetchall()
        }
        for stmt in MIGRATIONS:
            if stmt.startswith("ALTER TABLE reconcile_batches ADD COLUMN fold_rule"):
                if "fold_rule" in existing_cols:
                    continue
            conn.execute(stmt)
        conn.commit()
        if conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]:
            return
        conn.execute("INSERT INTO settings(key, value) VALUES ('dock_day', ?)", ("2026-11-18",))
        suppliers = [
            ("SUP-A", "东海食材"),
            ("SUP-B", "北岭包装"),
            ("SUP-C", "南湾百货"),
        ]
        for code, title in suppliers:
            conn.execute("INSERT INTO suppliers(code, title) VALUES (?,?)", (code, title))
        expects = [
            ("EX-1001", "SUP-A", "SKU-米-25", "2026-11-18", 40, "早班预报"),
            ("EX-1002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("EX-1003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("EX-1004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, None),
            ("EX-0901", "SUP-A", "SKU-米-25", "2026-11-17", 10, "昨日"),
        ]
        for row in expects:
            conn.execute(
                '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T08:00:00Z"),
            )
        receives = [
            ("RC-2001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "2026-11-18T09:10:00Z", "少2"),
            ("RC-2002", "SUP-A", "SKU-油-5", "2026-11-18", 20, "2026-11-18T09:20:00Z", None),
            ("RC-2003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, "2026-11-18T10:00:00Z", None),
            ("RC-2004", "SUP-C", "SKU-巾-卷", "2026-11-18", 66, "2026-11-18T11:00:00Z", "多6"),
        ]
        for row in receives:
            conn.execute(
                '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (*row, row[5]),
            )
        confirms = [
            ("CF-3001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "回执按实收"),
            ("CF-3002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("CF-3003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("CF-3004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, "回执仍报预报数"),
        ]
        for row in confirms:
            conn.execute(
                '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T12:00:00Z"),
            )
        # 夹具数据同步补批次快照：正式行与快照同口径（fold_rule=declared_dock_day，零折日）
        from app.services.daykey import DAY_KEY_RULE

        for doc_no, sup, sku, day, qty, _note in expects:
            conn.execute(
                '''INSERT INTO ingest_events(leg, line_no, supplier_code, sku, declared_dock_day,
                       closed_dock_day, occurred_at, qty, fold_rule, folded_count, result, reason, created_at)
                   VALUES ('expect',?,?,?,?,?,NULL,?,? ,0,'accepted',NULL,?)''',
                (doc_no, sup, sku, day, day, qty, DAY_KEY_RULE, "2026-11-18T08:00:00Z"),
            )
        for rc_no, sup, sku, day, qty, occurred, _note in receives:
            conn.execute(
                '''INSERT INTO ingest_events(leg, line_no, supplier_code, sku, declared_dock_day,
                       closed_dock_day, occurred_at, qty, fold_rule, folded_count, result, reason, created_at)
                   VALUES ('receive',?,?,?,?,?,?,?,? ,0,'accepted',NULL,?)''',
                (rc_no, sup, sku, day, day, occurred, qty, DAY_KEY_RULE, occurred),
            )
        for cf_no, sup, sku, day, qty, _note in confirms:
            conn.execute(
                '''INSERT INTO ingest_events(leg, line_no, supplier_code, sku, declared_dock_day,
                       closed_dock_day, occurred_at, qty, fold_rule, folded_count, result, reason, created_at)
                   VALUES ('confirm',?,?,?,?,?,NULL,?,? ,0,'accepted',NULL,?)''',
                (cf_no, sup, sku, day, day, qty, DAY_KEY_RULE, "2026-11-18T12:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()

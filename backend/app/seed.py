from __future__ import annotations
from app.db import connect
from app.services.daykey import DAY_KEY_RULE

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
  fold_rule TEXT NOT NULL DEFAULT 'first_complete_anchor_day'
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
CREATE TABLE IF NOT EXISTS day_anchors (
  id INTEGER PRIMARY KEY,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  anchor_day TEXT NOT NULL,
  anchor_leg TEXT NOT NULL,
  anchor_line_no TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(supplier_code, sku)
);
CREATE TABLE IF NOT EXISTS quarantine_lines (
  id INTEGER PRIMARY KEY,
  leg TEXT NOT NULL,
  line_no TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  declared_dock_day TEXT NOT NULL,
  anchor_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_anchor ON quarantine_lines(anchor_day);
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

# 旧库迁移：锚日架构新增表与列。每次口径升级只在此追加。
MIGRATIONS = [
    '''CREATE TABLE IF NOT EXISTS day_anchors (
  id INTEGER PRIMARY KEY,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  anchor_day TEXT NOT NULL,
  anchor_leg TEXT NOT NULL,
  anchor_line_no TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(supplier_code, sku)
)''',
    '''CREATE TABLE IF NOT EXISTS quarantine_lines (
  id INTEGER PRIMARY KEY,
  leg TEXT NOT NULL,
  line_no TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  declared_dock_day TEXT NOT NULL,
  anchor_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
)''',
    "CREATE INDEX IF NOT EXISTS idx_quarantine_anchor ON quarantine_lines(anchor_day)",
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
    # 旧库补批次口径列；历史批次保留旧标识只读，新批次一律首行锚日
    "ALTER TABLE reconcile_batches ADD COLUMN fold_rule TEXT NOT NULL DEFAULT 'declared_dock_day'",
]

def _backfill_anchors(conn) -> None:
    """旧库（架构切换前已有正式行）锚回填：每个业务键取最早完整落库行的 dock_day 立锚。"""
    anchored = {
        (r["supplier_code"], r["sku"])
        for r in conn.execute("SELECT supplier_code, sku FROM day_anchors").fetchall()
    }
    legs = [
        ("expect", "expect_lines", "doc_no", "created_at"),
        ("receive", "receive_lines", "receive_no", "created_at"),
        ("confirm", "confirm_lines", "confirm_no", "created_at"),
    ]
    keys = set()
    for _leg, table, _no, _ts in legs:
        for r in conn.execute(f"SELECT DISTINCT supplier_code, sku FROM {table}").fetchall():
            keys.add((r["supplier_code"], r["sku"]))
    for supplier_code, sku in sorted(keys - anchored):
        earliest = None
        for leg, table, no_col, ts_col in legs:
            r = conn.execute(
                f'''SELECT dock_day, {no_col} AS line_no, {ts_col} AS ts, id FROM {table}
                    WHERE supplier_code=? AND sku=? ORDER BY {ts_col}, id LIMIT 1''',
                (supplier_code, sku),
            ).fetchone()
            if r and (earliest is None or (r["ts"], r["id"]) < (earliest["ts"], earliest["id"])):
                row = dict(r)
                row["leg"] = leg
                earliest = row
        if earliest:
            conn.execute(
                '''INSERT INTO day_anchors(supplier_code, sku, anchor_day, anchor_leg, anchor_line_no, created_at)
                   VALUES (?,?,?,?,?,?)''',
                (supplier_code, sku, earliest["dock_day"], earliest["leg"],
                 earliest["line_no"], earliest["ts"]),
            )

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
        _backfill_anchors(conn)
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
            # 昨日夹具使用独立货号：同业务键不允许跨日，锚日架构下不能再与当日米-25 同键
            ("EX-0901", "SUP-A", "SKU-米-10", "2026-11-17", 10, "昨日"),
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
        # 锚表：每个业务键以最先完整落库行（预报腿）的业务日立锚
        anchors = [
            ("SUP-A", "SKU-米-25", "2026-11-18", "expect", "EX-1001"),
            ("SUP-A", "SKU-油-5", "2026-11-18", "expect", "EX-1002"),
            ("SUP-B", "SKU-箱-大", "2026-11-18", "expect", "EX-1003"),
            ("SUP-C", "SKU-巾-卷", "2026-11-18", "expect", "EX-1004"),
            ("SUP-A", "SKU-米-10", "2026-11-17", "expect", "EX-0901"),
        ]
        for supplier_code, sku, day, leg, line_no in anchors:
            conn.execute(
                '''INSERT INTO day_anchors(supplier_code, sku, anchor_day, anchor_leg, anchor_line_no, created_at)
                   VALUES (?,?,?,?,?,?)''',
                (supplier_code, sku, day, leg, line_no, "2026-11-18T08:00:00Z"),
            )
        # 装载快照：正式行与快照同口径（首行锚日，种子内零改挂）
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

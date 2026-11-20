"""端到端口径核对测试（全局唯一架构：首行锚日 first_complete_anchor_day）。

四个指定夹具：
1. test_three_legs_three_days_fold_to_anchor   —— 三行三日
2. test_scan_window_does_not_eat_wrong_day     —— 扫描吃错日
3. test_fold_after_anchor_sealed_goes_quarantine —— 封死后改挂
4. test_same_key_never_counted_on_two_days     —— 两日双计

另覆盖：occurred_at 绝不折日、三标签同源（扫描/闸/看板）、装载与封印并发无半截、
完整性校验能拦下被篡改的撕裂行、看板计数与正式表/快照严格相等、旧库锚回填。
"""
from __future__ import annotations
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="tridock-test-")
os.environ["TRIDOCK_DATA_DIR"] = _tmp

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import connect  # noqa: E402
from app.services.daykey import DAY_KEY_RULE  # noqa: E402

@pytest.fixture()
def client():
    with TestClient(app, base_url="http://test/api") as c:
        yield c

D1 = "2026-11-22"
D2 = "2026-11-23"
D3 = "2026-11-24"
SEAL_DAY = "2026-11-20"
RACE_DAY = "2026-11-25"
SCAN_D1 = "2026-12-01"
SCAN_D2 = "2026-12-02"
DBL_DAY = "2026-12-03"
TORN_D1 = "2026-12-05"
TORN_D2 = "2026-12-06"
SNAP_DAY = "2026-12-08"
CLOCK_DAY = "2026-12-09"
BOARD_DAY = "2026-12-10"
THREE_DAYS = ("2026-12-11", "2026-12-12", "2026-12-13")

def _expect(client, no, day, sku, sup="SUP-A", qty=5):
    return client.post("expects", json={
        "doc_no": no, "supplier_code": sup, "sku": sku, "dock_day": day, "qty": qty,
    })

def _receive(client, no, day, sku, sup="SUP-A", qty=5, occurred="2026-12-31T03:30:00Z"):
    return client.post("receives", json={
        "receive_no": no, "supplier_code": sup, "sku": sku,
        "dock_day": day, "qty": qty, "occurred_at": occurred,
    })

def _confirm(client, no, day, sku, sup="SUP-A", qty=5):
    return client.post("confirms", json={
        "confirm_no": no, "supplier_code": sup, "sku": sku, "dock_day": day, "qty": qty,
    })

def _seal_day(client, day):
    """跑对账并处置全部差额后封印；日已封印时直接复用该状态。"""
    batches = client.get(f"reconcile/batches?dock_day={day}").json()
    if any(b["status"] == "sealed" for b in batches):
        return
    r = client.post("reconcile/run", json={"dock_day": day})
    assert r.status_code == 200, r.text
    batch = r.json()
    if batch["variance_count"]:
        for v in client.get(f"reconcile/variances?batch_id={batch['batch_id']}").json():
            rr = client.post(f"reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
            assert rr.status_code == 200, rr.text
    r = client.post("reconcile/seal", json={"dock_day": day})
    assert r.status_code == 200, r.text

def _anchor_of(sku, sup="SUP-A"):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM day_anchors WHERE supplier_code=? AND sku=?", (sup, sku)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

# --- 夹具 1：三行三日 -----------------------------------------------------------

def test_three_legs_three_days_fold_to_anchor(client):
    sku = "SKU-THREE"
    r1 = _expect(client, "EX-3D-1", THREE_DAYS[0], sku)
    assert r1.status_code == 200 and r1.json()["folded"] is False
    r2 = _receive(client, "RC-3D-1", THREE_DAYS[1], sku)   # 申报次日
    assert r2.status_code == 200
    assert r2.json()["folded"] is True
    assert r2.json()["anchor_day"] == THREE_DAYS[0] and r2.json()["dock_day"] == THREE_DAYS[0]
    r3 = _confirm(client, "CF-3D-1", THREE_DAYS[2], sku)   # 申报第三日
    assert r3.status_code == 200 and r3.json()["dock_day"] == THREE_DAYS[0]

    # 锚：最先完整落库的预报行立锚，锚日 THREE_DAYS[0]，之后不可变
    anchor = _anchor_of(sku)
    assert anchor["anchor_day"] == THREE_DAYS[0] and anchor["anchor_leg"] == "expect"
    assert anchor["anchor_line_no"] == "EX-3D-1"

    # 三张正式表的同键行全部挂锚日 THREE_DAYS[0]，申报日 THREE_DAYS[1]/THREE_DAYS[2] 下查无此行
    assert all(x["dock_day"] == THREE_DAYS[0] for x in client.get(f"expects?dock_day={THREE_DAYS[0]}").json()
               if x["doc_no"] == "EX-3D-1")
    for day, path, no in [(THREE_DAYS[1], "receives", "RC-3D-1"), (THREE_DAYS[2], "confirms", "CF-3D-1")]:
        assert all(x[next(k for k in x if k.endswith("_no"))] != no
                   for x in client.get(f"{path}?dock_day={day}").json())
    assert any(x["receive_no"] == "RC-3D-1" and x["dock_day"] == THREE_DAYS[0]
               for x in client.get(f"receives?dock_day={THREE_DAYS[0]}").json())

    # 快照留改挂痕迹：申报日原样、闭包日=锚日、folded_count=1
    events = client.get(f"ingest-events?dock_day={THREE_DAYS[0]}").json()
    ev_rc = next(e for e in events if e["line_no"] == "RC-3D-1")
    ev_cf = next(e for e in events if e["line_no"] == "CF-3D-1")
    assert ev_rc["declared_dock_day"] == THREE_DAYS[1] and ev_rc["closed_dock_day"] == THREE_DAYS[0]
    assert ev_rc["folded_count"] == 1 and ev_rc["result"] == "accepted"
    assert ev_cf["declared_dock_day"] == THREE_DAYS[2] and ev_cf["folded_count"] == 1
    assert all(e["fold_rule"] == DAY_KEY_RULE for e in events if e["line_no"].startswith(("EX-3D", "RC-3D", "CF-3D")))

    # 扫描只认锚日：THREE_DAYS[0] 对账三行合拢且对齐；THREE_DAYS[1]/THREE_DAYS[2] 批次里根本没有这个键
    r = client.post("reconcile/run", json={"dock_day": THREE_DAYS[0]})
    assert r.status_code == 200
    m = next(x for x in client.get(f"reconcile/matches?batch_id={r.json()['batch_id']}").json()
             if x["sku"] == sku)
    assert m["dock_day"] == THREE_DAYS[0] and m["aligned"] == 1
    for day in (THREE_DAYS[1], THREE_DAYS[2]):
        rr = client.post("reconcile/run", json={"dock_day": day})
        assert rr.status_code == 200
        mm = client.get(f"reconcile/matches?batch_id={rr.json()['batch_id']}").json()
        assert all(x["sku"] != sku for x in mm)

# --- 夹具 2：扫描吃错日 ---------------------------------------------------------

def test_scan_window_does_not_eat_wrong_day(client):
    sku = "SKU-SCAN"
    assert _expect(client, "EX-SCAN-1", SCAN_D1, sku).status_code == 200
    r = _receive(client, "RC-SCAN-1", SCAN_D2, sku)  # 扫描窗若按申报日 SCAN_D2 收，就吃错日
    assert r.status_code == 200 and r.json()["dock_day"] == SCAN_D1

    # D2 窗口吞不下这行；D1 窗口吞得到
    assert all(x["receive_no"] != "RC-SCAN-1" for x in client.get(f"receives?dock_day={SCAN_D2}").json())
    assert any(x["receive_no"] == "RC-SCAN-1" for x in client.get(f"receives?dock_day={SCAN_D1}").json())

    # 看板/日列表同标签：把 settings 拨到 D2，卡片不得为该键加一
    assert client.post("settings/dock-day", json={"dock_day": SCAN_D2}).status_code == 200
    dash_d2 = client.get("/dashboard").json()
    assert dash_d2["fold_rule"] == DAY_KEY_RULE
    assert dash_d2["receive_count"] == len(client.get(f"receives?dock_day={SCAN_D2}").json())
    assert all(x["receive_no"] != "RC-SCAN-1" for x in client.get(f"receives?dock_day={SCAN_D2}").json())
    assert client.post("settings/dock-day", json={"dock_day": SCAN_D1}).status_code == 200
    dash_d1 = client.get("/dashboard").json()
    assert any(x["receive_no"] == "RC-SCAN-1" for x in client.get(f"receives?dock_day={SCAN_D1}").json())
    assert dash_d1["counts_consistent"] is True

    # 闸位图同标签：D1 未封印可写；封印 D1 后同键改挂立即被拦（夹具 3 详述）
    assert dash_d1["sealed"] is False

# --- 夹具 3：封死后改挂进隔离 ---------------------------------------------------

def test_fold_after_anchor_sealed_goes_quarantine(client):
    sku = "SKU-SEAL"
    assert _expect(client, "EX-SEAL-1", SEAL_DAY, sku).status_code == 200
    _seal_day(client, SEAL_DAY)
    assert client.get("/dashboard").json()  # 不依赖 settings 日，直接查闸
    conn = connect()
    try:
        sealed = conn.execute(
            "SELECT status FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
            (SEAL_DAY,),
        ).fetchone()["status"]
    finally:
        conn.close()
    assert sealed == "sealed"

    # 同键后续行申报别的开放日：锚日已封，改挂失败 → 409 + 隔离，正式表零写入
    r = _receive(client, "RC-SEAL-1", D2, sku)
    assert r.status_code == 409 and "隔离" in r.json()["detail"]
    assert all(x["receive_no"] != "RC-SEAL-1"
               for x in client.get(f"receives?dock_day={D2}").json())
    assert all(x["receive_no"] != "RC-SEAL-1"
               for x in client.get(f"receives?dock_day={SEAL_DAY}").json())

    q = client.get(f"/quarantine?anchor_day={SEAL_DAY}").json()
    qrow = next(x for x in q if x["line_no"] == "RC-SEAL-1")
    assert qrow["declared_dock_day"] == D2 and qrow["anchor_day"] == SEAL_DAY
    ev = next(e for e in client.get(f"ingest-events?dock_day={SEAL_DAY}&result=quarantined_sealed").json()
              if e["line_no"] == "RC-SEAL-1")
    assert ev["declared_dock_day"] == D2 and ev["closed_dock_day"] == SEAL_DAY
    assert ev["folded_count"] == 1

    # 三腿各自都试一次：回执申报 D3 同样隔离
    r = _confirm(client, "CF-SEAL-1", D3, sku)
    assert r.status_code == 409
    assert any(x["line_no"] == "CF-SEAL-1" for x in client.get("/quarantine").json())

    # 封印日上的全新业务键（无锚）：禁止立锚，400 + rejected_sealed，同样不留正式行
    r = _receive(client, "RC-NEWKEY-SEAL", SEAL_DAY, "SKU-NEW-SEAL")
    assert r.status_code == 400 and "封印" in r.json()["detail"]
    assert all(x["receive_no"] != "RC-NEWKEY-SEAL" for x in client.get("receives").json())
    ev2 = next(e for e in client.get(f"ingest-events?dock_day={SEAL_DAY}&result=rejected_sealed").json()
               if e["line_no"] == "RC-NEWKEY-SEAL")
    assert ev2["closed_dock_day"] == SEAL_DAY

    # 被隔离的行不进任何匹配桶，D2/D3 自己的其他键照常工作（无连带伤害）
    assert _expect(client, "EX-OTHER-DAY", D2, "SKU-OPEN-OK").status_code == 200

# --- 夹具 4：两日双计 -----------------------------------------------------------

def test_same_key_never_counted_on_two_days(client):
    sku = "SKU-DBL"
    dbl_other = "2026-12-04"
    assert _expect(client, "EX-DBL-1", DBL_DAY, sku, qty=5).status_code == 200
    assert _receive(client, "RC-DBL-1", dbl_other, sku, qty=5).status_code == 200   # 改挂锚日
    assert _receive(client, "RC-DBL-2", dbl_other, sku, qty=3).status_code == 200   # 同键再一笔，仍锚日

    conn = connect()
    try:
        days = {r["dock_day"] for r in conn.execute(
            "SELECT dock_day FROM receive_lines WHERE sku=?", (sku,)).fetchall()}
        d1_formal = sum(conn.execute(
            f"SELECT COUNT(*) c FROM {t} WHERE dock_day=?", (DBL_DAY,)).fetchone()["c"]
            for t in ("expect_lines", "receive_lines", "confirm_lines"))
        d1_accepted = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND result='accepted'",
            (DBL_DAY,)).fetchone()["c"]
        d2_accepted_key = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE closed_dock_day=? AND sku=? AND result='accepted'",
            (dbl_other, sku)).fetchone()["c"]
    finally:
        conn.close()
    assert days == {DBL_DAY}               # 同键只可能出现在锚日一天
    assert d2_accepted_key == 0            # 次日没有吞过这个键任何一行
    # 全日不变量：锚日正式行数 == 锚日 accepted 快照数（含其他用例在当日的行，全局对照）
    assert d1_formal == d1_accepted

    # 封印后再想塞进次日：进隔离，两天都不计
    _seal_day(client, DBL_DAY)
    r = _receive(client, "RC-DBL-3", dbl_other, sku, qty=1)
    assert r.status_code == 409
    conn = connect()
    try:
        n_formal = conn.execute(
            "SELECT COUNT(*) c FROM receive_lines WHERE receive_no='RC-DBL-3'").fetchone()["c"]
        n_quar = conn.execute(
            "SELECT COUNT(*) c FROM quarantine_lines WHERE line_no='RC-DBL-3'").fetchone()["c"]
    finally:
        conn.close()
    assert n_formal == 0 and n_quar == 1

# --- 三标签同源：闸就在扫描日当天 ----------------------------------------------

def test_gate_bites_on_the_same_label_as_scan(client):
    day = "2026-11-26"
    sku = "SKU-GATE"
    assert _expect(client, "EX-GATE-1", day, sku).status_code == 200
    _seal_day(client, day)
    # 旧实现闸位在 day+1，当天装载拦不住；现在闸与扫描同标签，当天同键必被拦
    r = _receive(client, "RC-GATE-1", day, sku)
    assert r.status_code == 409
    r = _receive(client, "RC-GATE-2", "2026-11-27", sku)  # 申报次日改挂同样被拦
    assert r.status_code == 409

# --- 完整性校验：撕裂行无法蒙混过关 --------------------------------------------

def test_reconcile_rejects_torn_cross_day_rows(client):
    sku = "SKU-TORN"
    assert _expect(client, "EX-TORN-1", TORN_D1, sku).status_code == 200
    # 绕过装载口直接塞一条撕到 D2 的正式行（模拟标签撕裂/历史脏数据）
    conn = connect()
    try:
        conn.execute(
            '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
               VALUES ('RC-TORN-BAD','SUP-A',?,?,4,'2026-11-23T09:00:00Z',NULL,'2026-11-23T09:00:00Z')''',
            (sku, TORN_D2),
        )
        conn.commit()
    finally:
        conn.close()
    # 两个日的对账都必须拒绝，且不产出任何新批次
    for day in (TORN_D1, TORN_D2):
        before = len(client.get(f"reconcile/batches?dock_day={day}").json())
        r = client.post("reconcile/run", json={"dock_day": day})
        assert r.status_code == 400 and "完整性校验失败" in r.json()["detail"]
        assert len(client.get(f"reconcile/batches?dock_day={day}").json()) == before
    # 封印路径同样被真相校验拦住
    r = client.post("reconcile/seal", json={"dock_day": TORN_D1})
    assert r.status_code == 400 and "完整性校验失败" in r.json()["detail"]
    # 清掉脏行后恢复正常
    conn = connect()
    try:
        conn.execute("DELETE FROM receive_lines WHERE receive_no='RC-TORN-BAD'")
        conn.commit()
    finally:
        conn.close()
    assert client.post("reconcile/run", json={"dock_day": TORN_D1}).status_code == 200

def test_reconcile_rejects_snapshot_count_mismatch(client):
    day = SNAP_DAY
    sku = "SKU-SNAP"
    assert _expect(client, "EX-SNAP-1", day, sku).status_code == 200
    conn = connect()
    try:
        # 抹掉一条 accepted 快照：正式行还在，快照数对不上，结案日键集合即撒谎
        conn.execute("DELETE FROM ingest_events WHERE line_no='EX-SNAP-1'")
        conn.commit()
    finally:
        conn.close()
    r = client.post("reconcile/run", json={"dock_day": day})
    assert r.status_code == 400 and "正式行" in r.json()["detail"]

# --- 并发：装载/改挂与封印交错，每笔恰一个终态，无半截 --------------------------
#
# 注意：真实部署（uvicorn）每个请求一条独立 sqlite 连接，因此并发用例必须让每个
# 工作线程自持连接、直打服务层；TestClient 门户共享单连接，多线程共用会制造
# 与生产无关的假象，不能用于验证写事务串行化。

def _service_receive(conn, no, day, sku, sup="SUP-A", qty=9, occurred="2026-12-31T03:30:00Z"):
    from app.services import anchor
    return anchor.ingest_leg(
        conn, leg="receive", line_no=no, supplier_code=sup, sku=sku,
        declared_dock_day=day, qty=qty, occurred_at=occurred,
        build_insert=lambda closed_day: (
            '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            (no, sup, sku, closed_day, qty, occurred, None, "2026-12-31T03:30:00Z"),
        ),
    )

def test_concurrent_ingest_vs_seal_no_half_writes(client):
    # 先经 HTTP 铺锚（预报腿），保证业务键已有锚日
    assert _expect(client, "EX-RACE-0", RACE_DAY, "SKU-RACE", qty=9).status_code == 200
    barrier = threading.Barrier(3)

    def ingest(idx):
        barrier.wait()
        conn = connect()  # 每线程独立连接，等同真实请求
        try:
            out = _service_receive(conn, f"RC-RACE-{idx}", RACE_DAY, "SKU-RACE")
            return (200, out["dock_day"])
        except Exception as e:
            return (type(e).__name__, str(e)[:80])
        finally:
            conn.close()

    def seal():
        barrier.wait()
        conn = connect()
        try:
            from app.services import reconcile_service as svc
            try:
                svc.run_reconcile(conn, RACE_DAY)
            except ValueError:
                return ("run_rejected",)
            batch = conn.execute(
                "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
                (RACE_DAY,)).fetchone()
            for v in conn.execute(
                "SELECT id FROM variance_rows WHERE batch_id=? AND fate='pending'",
                (batch["id"],)).fetchall():
                svc.set_variance_fate(conn, v["id"], "accept_short")
            svc.seal_day(conn, RACE_DAY)
            return ("sealed",)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(ingest, 1)
        f2 = pool.submit(ingest, 2)
        fs = pool.submit(seal)
        outcomes = [f1.result(), f2.result(), fs.result()]

    conn = connect()
    try:
        landed = {r["receive_no"] for r in conn.execute(
            "SELECT receive_no FROM receive_lines WHERE receive_no IN ('RC-RACE-1','RC-RACE-2')"
        ).fetchall()}
        accepted = {r["line_no"] for r in conn.execute(
            "SELECT line_no FROM ingest_events WHERE line_no IN ('RC-RACE-1','RC-RACE-2') AND result='accepted'"
        ).fetchall()}
        quarantined = {r["line_no"] for r in conn.execute(
            "SELECT line_no FROM ingest_events WHERE line_no IN ('RC-RACE-1','RC-RACE-2') AND result='quarantined_sealed'"
        ).fetchall()}
        rejected = {r["line_no"] for r in conn.execute(
            "SELECT line_no FROM ingest_events WHERE line_no IN ('RC-RACE-1','RC-RACE-2') AND result='rejected_sealed'"
        ).fetchall()}
        quar_rows = {r["line_no"] for r in conn.execute(
            "SELECT line_no FROM quarantine_lines WHERE line_no IN ('RC-RACE-1','RC-RACE-2')"
        ).fetchall()}
    finally:
        conn.close()

    # 每笔装载恰一个终态：200↔正式行+accepted；AnchorQuarantineError↔隔离+quarantined_sealed
    assert outcomes[2][0] in ("sealed", "run_rejected")
    assert landed == accepted
    assert accepted.isdisjoint(quarantined | rejected)
    assert quarantined == quar_rows                      # 隔离区与快照一一对应
    assert accepted | quarantined | rejected == {"RC-RACE-1", "RC-RACE-2"}
    # 不存在"快照 accepted 但正式行缺失"或反过来的半截状态
    assert len(accepted) + len(quarantined) + len(rejected) == 2

def test_concurrent_same_unique_no_one_wins_cleanly(client):
    day = "2026-11-28"
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        conn = connect()  # 每线程独立连接
        try:
            try:
                _service_receive(conn, "RC-DUP-ANCHOR", day, "SKU-DUP-ANCHOR", qty=2)
                return 200
            except Exception:
                return 400
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: go(), range(2)))
    assert sorted(codes) == [200, 400]
    conn = connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM receive_lines WHERE receive_no='RC-DUP-ANCHOR'").fetchone()["c"]
        accepted = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE line_no='RC-DUP-ANCHOR' AND result='accepted'"
        ).fetchone()["c"]
        anchors = conn.execute(
            "SELECT COUNT(*) c FROM day_anchors WHERE sku='SKU-DUP-ANCHOR'").fetchone()["c"]
    finally:
        conn.close()
    assert n == 1 and accepted == 1 and anchors == 1

# --- 输入口径与 occurred_at 审计 -----------------------------------------------

def test_invalid_day_and_timestamp_rejected(client):
    r = _receive(client, "RC-BAD-D", "2026-13-40", "SKU-BAD")
    assert r.status_code == 400
    r = _receive(client, "RC-BAD-T", CLOCK_DAY, "SKU-BAD", occurred="not-a-timestamp")
    assert r.status_code == 400
    r = client.post("settings/dock-day", json={"dock_day": "11/18/2026"})
    assert r.status_code == 400

def test_occurred_at_never_drives_anchor_day(client):
    sku = "SKU-CLOCK"
    r = _receive(client, "RC-CLOCK-1", CLOCK_DAY, sku, occurred="2030-01-01T00:00:00Z")
    assert r.status_code == 200 and r.json()["dock_day"] == CLOCK_DAY
    row = next(x for x in client.get(f"receives?dock_day={CLOCK_DAY}").json()
               if x["receive_no"] == "RC-CLOCK-1")
    assert row["occurred_at"] == "2030-01-01T00:00:00Z"  # 原样审计留存
    assert _anchor_of(sku)["anchor_day"] == CLOCK_DAY

# --- 看板/列表/快照三方计数一致 ------------------------------------------------

def test_dashboard_lists_and_snapshot_share_one_truth(client):
    assert client.post("settings/dock-day", json={"dock_day": BOARD_DAY}).status_code == 200
    dash = client.get("/dashboard").json()
    assert dash["fold_rule"] == DAY_KEY_RULE
    assert dash["expect_count"] == len(client.get(f"expects?dock_day={BOARD_DAY}").json())
    assert dash["receive_count"] == len(client.get(f"receives?dock_day={BOARD_DAY}").json())
    assert dash["confirm_count"] == len(client.get(f"confirms?dock_day={BOARD_DAY}").json())
    assert dash["formal_total"] == dash["ingest_accepted_count"]
    assert dash["counts_consistent"] is True
    # 隔离计数上墙：隔离行不计入任何正式闭包
    assert dash["quarantine_count"] == len(client.get(f"/quarantine?anchor_day={BOARD_DAY}").json())

# --- 旧库迁移：无锚历史库启动时按最早行回填锚 ----------------------------------

def test_legacy_db_anchor_backfill(monkeypatch):
    import app.config as cfg
    import app.db as dbmod
    from app import seed

    legacy_dir = Path(tempfile.mkdtemp(prefix="tridock-legacy-"))
    monkeypatch.setattr(cfg, "DATA_DIR", legacy_dir)
    monkeypatch.setattr(cfg, "DB_PATH", legacy_dir / "legacy.db")
    monkeypatch.setattr(dbmod, "DB_PATH", legacy_dir / "legacy.db")

    conn = dbmod.connect()
    conn.executescript('''
    CREATE TABLE suppliers(id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT);
    CREATE TABLE expect_lines(id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE, supplier_code TEXT,
      sku TEXT, dock_day TEXT, qty REAL, note TEXT, created_at TEXT);
    CREATE TABLE receive_lines(id INTEGER PRIMARY KEY, receive_no TEXT UNIQUE, supplier_code TEXT,
      sku TEXT, dock_day TEXT, qty REAL, occurred_at TEXT, note TEXT, created_at TEXT);
    CREATE TABLE confirm_lines(id INTEGER PRIMARY KEY, confirm_no TEXT UNIQUE, supplier_code TEXT,
      sku TEXT, dock_day TEXT, qty REAL, note TEXT, created_at TEXT);
    CREATE TABLE reconcile_batches(id INTEGER PRIMARY KEY, batch_no TEXT UNIQUE, dock_day TEXT,
      matched_count INTEGER, variance_count INTEGER, status TEXT, sealed_at TEXT, note TEXT);
    CREATE TABLE match_rows(id INTEGER PRIMARY KEY, batch_id INTEGER, dock_day TEXT,
      supplier_code TEXT, sku TEXT, expect_qty REAL, receive_qty REAL, confirm_qty REAL, aligned INTEGER);
    CREATE TABLE variance_rows(id INTEGER PRIMARY KEY, batch_id INTEGER, dock_day TEXT,
      supplier_code TEXT, sku TEXT, expect_qty REAL, receive_qty REAL, confirm_qty REAL,
      fate TEXT, created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE late_spill(id INTEGER PRIMARY KEY, dock_day TEXT, reason TEXT);
    CREATE TABLE reopen_requests(id INTEGER PRIMARY KEY, dock_day TEXT, status TEXT DEFAULT 'none');
    INSERT INTO suppliers(code, title) VALUES ('SUP-A','旧库供应商');
    INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, created_at)
      VALUES ('EX-OLD','SUP-A','SKU-OLD','2026-11-17',5,'2026-11-17T08:00:00Z');
    INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, created_at)
      VALUES ('RC-OLD','SUP-A','SKU-OLD','2026-11-18',5,'2026-11-18T09:00:00Z','2026-11-18T09:00:00Z');
    ''')
    conn.commit()
    conn.close()

    seed.seed_if_empty()  # 迁移 + 锚回填；suppliers 非空，不会灌种子夹具
    conn = dbmod.connect()
    try:
        row = conn.execute(
            "SELECT * FROM day_anchors WHERE supplier_code='SUP-A' AND sku='SKU-OLD'").fetchone()
        assert row is not None
        assert row["anchor_day"] == "2026-11-17"          # 最早完整落库行的业务日
        assert row["anchor_leg"] == "expect" and row["anchor_line_no"] == "EX-OLD"
    finally:
        conn.close()

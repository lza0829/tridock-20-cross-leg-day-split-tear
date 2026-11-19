"""端到端口径核对测试（架构 A：申报 dock_day 为唯一日闭包键）。

覆盖：
1. occurred_at 与申报日不同：三腿仍落申报日，occurred_at 仅审计；
2. 已封印申报日不能被撬写（三腿各自），拒绝留 rejected_sealed 快照、正式表零写入；
3. 总览与按日列表同日计数一致；
4. 对齐键集合随落日变化可对照（跨日不串键）；
5. 并发两笔装载与封印交错：不双写、不半截，要么进开放日要么被拒留痕；
6. 批次快照 fold_rule=declared_dock_day、折日笔数为 0。
"""
from __future__ import annotations
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

_tmp = tempfile.mkdtemp(prefix="tridock-test-")
os.environ["TRIDOCK_DATA_DIR"] = _tmp

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import connect  # noqa: E402

@pytest.fixture()
def client():
    with TestClient(app, base_url="http://test/api") as c:
        yield c

DAY = "2026-11-18"
OTHER = "2026-11-19"

def _receive(client, no, day=DAY, occurred="2026-11-20T03:30:00Z", sku="SKU-T1", sup="SUP-A", qty=5):
    return client.post("receives", json={
        "receive_no": no, "supplier_code": sup, "sku": sku,
        "dock_day": day, "qty": qty, "occurred_at": occurred,
    })

def _seal_day(client, day):
    """跑对账并处置全部差额后封印；日已被前序用例封印时直接复用该状态。"""
    batches = client.get(f"reconcile/batches?dock_day={day}").json()
    if any(b["status"] == "sealed" for b in batches):
        return {"dock_day": day, "status": "sealed"}
    r = client.post("reconcile/run", json={"dock_day": day})
    assert r.status_code == 200, r.text
    batch = r.json()
    if batch["variance_count"]:
        variances = client.get(f"reconcile/variances?batch_id={batch['batch_id']}").json()
        for v in variances:
            rr = client.post(f"reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
            assert rr.status_code == 200, rr.text
    r = client.post("reconcile/seal", json={"dock_day": day})
    assert r.status_code == 200, r.text
    return r.json()

# --- 1. occurred_at 与申报日不同：闭包日恒为申报日 -----------------------------

def test_receive_uses_declared_day_not_occurred_at(client):
    r = _receive(client, "RC-T-001", day=DAY, occurred="2026-11-20T03:30:00Z")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dock_day"] == DAY
    assert body["fold_rule"] == "declared_dock_day"
    assert body["folded"] is False

    # 正式表按申报日闭包，按 occurred_at 的日历日查不到
    same_day = client.get(f"receives?dock_day={DAY}").json()
    assert any(x["receive_no"] == "RC-T-001" and x["dock_day"] == DAY for x in same_day)
    other_day = client.get(f"receives?dock_day={OTHER}").json()
    assert all(x["receive_no"] != "RC-T-001" for x in other_day)

    # occurred_at 原样审计留存、未被改写
    row = next(x for x in same_day if x["receive_no"] == "RC-T-001")
    assert row["occurred_at"] == "2026-11-20T03:30:00Z"

    events = client.get(f"ingest-events?dock_day={DAY}&leg=receive").json()
    ev = next(e for e in events if e["line_no"] == "RC-T-001")
    assert ev["declared_dock_day"] == DAY
    assert ev["closed_dock_day"] == DAY
    assert ev["fold_rule"] == "declared_dock_day"
    assert ev["folded_count"] == 0
    assert ev["result"] == "accepted"
    assert ev["occurred_at"] == "2026-11-20T03:30:00Z"

def test_three_legs_share_same_declared_closure(client):
    # 三腿申报同一日、实收 occurred_at 跨日，对齐键集合必须在申报日闭包内合拢
    client.post("expects", json={"doc_no": "EX-T1", "supplier_code": "SUP-A",
                                  "sku": "SKU-X1", "dock_day": OTHER, "qty": 7})
    _receive(client, "RC-T1", day=OTHER, occurred="2026-10-01T00:00:00Z", sku="SKU-X1", qty=7)
    client.post("confirms", json={"confirm_no": "CF-T1", "supplier_code": "SUP-A",
                                   "sku": "SKU-X1", "dock_day": OTHER, "qty": 7})
    r = client.post("reconcile/run", json={"dock_day": OTHER})
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    matches = client.get(f"reconcile/matches?batch_id={batch_id}").json()
    m = next(x for x in matches if x["sku"] == "SKU-X1")
    assert m["dock_day"] == OTHER
    assert (m["expect_qty"], m["receive_qty"], m["confirm_qty"]) == (7.0, 7.0, 7.0)
    assert m["aligned"] == 1

# --- 2. 已封印日拒绝任何一腿写入，且留痕不留半截 -------------------------------

def test_sealed_day_rejects_all_legs_and_records_snapshot(client):
    _seal_day(client, DAY)
    dash = client.get("dashboard").json()
    assert dash["sealed"] is True

    payloads = [
        ("POST", "expects", {"doc_no": "EX-SEAL", "supplier_code": "SUP-A",
                             "sku": "SKU-S", "dock_day": DAY, "qty": 1}),
        ("POST", "receives", {"receive_no": "RC-SEAL", "supplier_code": "SUP-A",
                              "sku": "SKU-S", "dock_day": DAY, "qty": 1,
                              "occurred_at": "2026-11-19T08:00:00Z"}),
        ("POST", "confirms", {"confirm_no": "CF-SEAL", "supplier_code": "SUP-A",
                              "sku": "SKU-S", "dock_day": DAY, "qty": 1}),
    ]
    for _method, path, payload in payloads:
        r = client.post(path, json=payload)
        assert r.status_code == 400
        assert "封印" in r.json()["detail"]

    # 正式表零写入：列表里查不到任何被拒单号
    assert all(x["doc_no"] != "EX-SEAL" for x in client.get(f"expects?dock_day={DAY}").json())
    assert all(x["receive_no"] != "RC-SEAL" for x in client.get(f"receives?dock_day={DAY}").json())
    assert all(x["confirm_no"] != "CF-SEAL" for x in client.get(f"confirms?dock_day={DAY}").json())

    # 快照留三条 rejected_sealed，闭包日仍记申报日，折日笔数 0
    rejected = client.get(f"ingest-events?dock_day={DAY}&result=rejected_sealed").json()
    assert {e["line_no"] for e in rejected} == {"EX-SEAL", "RC-SEAL", "CF-SEAL"}
    assert all(e["closed_dock_day"] == DAY and e["folded_count"] == 0 for e in rejected)

    # 总览被拒计数
    dash = client.get("dashboard").json()
    assert dash["ingest_rejected_sealed_count"] >= 3

def test_sealed_day_cannot_be_pried_open_by_cross_day_occurred_at(client):
    # 封印后，拿一个 occurred_at 落在别的开放日的实收去撬申报日：必须拒，不允许改挂
    _seal_day(client, DAY)
    r = _receive(client, "RC-PRY", day=DAY, occurred="2026-11-19T23:59:00Z", sku="SKU-PRY")
    assert r.status_code == 400
    # 开放日下也没有被"改挂"进去
    assert all(x["receive_no"] != "RC-PRY" for x in client.get(f"receives?dock_day={OTHER}").json())

# --- 3. 总览与按日列表同日计数一致 ---------------------------------------------

def test_dashboard_counts_match_per_day_lists(client):
    # 夹具 + 本文件前面用例的写入都在同一库；以接口两侧实时计数对照
    dash = client.get("dashboard").json()
    day = dash["dock_day"]
    assert dash["fold_rule"] == "declared_dock_day"
    assert dash["expect_count"] == len(client.get(f"expects?dock_day={day}").json())
    assert dash["receive_count"] == len(client.get(f"receives?dock_day={day}").json())
    assert dash["confirm_count"] == len(client.get(f"confirms?dock_day={day}").json())

    # 快照 accepted 数 == 三腿正式表当日总行数
    accepted = client.get(f"ingest-events?dock_day={day}&result=accepted").json()
    assert len(accepted) == dash["expect_count"] + dash["receive_count"] + dash["confirm_count"]

# --- 4. 对齐键集合随落日变化：跨日不串键 ---------------------------------------

def test_reconcile_keys_stay_inside_declared_day(client):
    # 同供应商同货号，分别申报两个日闭包；两日对账互不串键
    for day, no in [(DAY, "RC-K1"), (OTHER, "RC-K2")]:
        r = _receive(client, no, day=day, occurred=f"{day}T10:00:00Z", sku="SKU-K")
        # DAY 已封印的用例可能先跑，做软断言处理：封印日拒绝则跳过该腿
        if r.status_code == 400:
            assert "封印" in r.json()["detail"]
    conn = connect()
    try:
        days = {r["dock_day"] for r in conn.execute(
            "SELECT DISTINCT dock_day FROM receive_lines WHERE sku='SKU-K'").fetchall()}
    finally:
        conn.close()
    # 落进去的行只可能挂在自己申报的日，绝不会出现 occurred_at 折出来的第三种日
    assert days.issubset({DAY, OTHER})

# --- 5. 并发：两笔装载 + 封印交错，不双写半截 -----------------------------------

def test_concurrent_ingest_vs_seal_no_half_writes(client):
    # 用一个全新开放日，先铺一腿可对齐数据
    day = "2026-11-20"
    client.post("expects", json={"doc_no": "EX-C0", "supplier_code": "SUP-A",
                                  "sku": "SKU-C", "dock_day": day, "qty": 9})
    client.post("confirms", json={"confirm_no": "CF-C0", "supplier_code": "SUP-A",
                                   "sku": "SKU-C", "dock_day": day, "qty": 9})

    barrier = threading.Barrier(3)

    def ingest(idx):
        barrier.wait()
        return client.post("receives", json={
            "receive_no": f"RC-C{idx}", "supplier_code": "SUP-A", "sku": f"SKU-C{idx}",
            "dock_day": day, "qty": 3, "occurred_at": "2026-12-01T00:00:00Z",
        }).status_code

    def seal():
        barrier.wait()
        # 先跑对账（无差额即可封印）；若装载先到导致出现差额，封印会报 400，属合法串行结果
        run = client.post("reconcile/run", json={"dock_day": day})
        if run.status_code != 200:
            return ("run_rejected", run.status_code)
        batch = run.json()
        if batch["variance_count"]:
            for v in client.get(f"reconcile/variances?batch_id={batch['batch_id']}").json():
                client.post(f"reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
        s = client.post("reconcile/seal", json={"dock_day": day})
        return ("seal", s.status_code)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(ingest, 1)
        f2 = pool.submit(ingest, 2)
        fs = pool.submit(seal)
        codes = [f1.result(), f2.result()]
        seal_outcome = fs.result()

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT receive_no, dock_day FROM receive_lines WHERE receive_no IN ('RC-C1','RC-C2')"
        ).fetchall()
        events = conn.execute(
            "SELECT line_no, result FROM ingest_events WHERE line_no IN ('RC-C1','RC-C2')"
        ).fetchall()
        sealed_row = conn.execute(
            "SELECT status FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1", (day,)
        ).fetchone()
    finally:
        conn.close()

    sealed = sealed_row is not None and sealed_row["status"] == "sealed"
    landed = {r["receive_no"] for r in rows}
    snap = {e["line_no"]: e["result"] for e in events}

    # 每笔装载有且仅有一条终态：200→正式行+accepted；400→无正式行+rejected_sealed
    for idx, code in zip((1, 2), codes):
        no = f"RC-C{idx}"
        if code == 200:
            assert no in landed and snap[no] == "accepted"
        else:
            assert code == 400
            assert no not in landed and snap[no] == "rejected_sealed"
    # 没有"快照 accepted 但正式行缺失"或反过来的半截状态
    assert landed == {no for no, result in snap.items() if result == "accepted"}

    if sealed:
        # 封印成功后，两笔要么封印前已进、要么被拒；封印后该日再写必拒
        r = client.post("receives", json={
            "receive_no": "RC-C3", "supplier_code": "SUP-A", "sku": "SKU-C3",
            "dock_day": day, "qty": 1, "occurred_at": "2026-12-02T00:00:00Z"})
        assert r.status_code == 400 and "封印" in r.json()["detail"]
    else:
        assert seal_outcome[1] == 400  # 封印因对账/差额串行而未成，数据本身完整可重试

def test_concurrent_two_ingests_same_unique_no_one_wins_cleanly(client):
    # 同单号并发：唯一约束 + 事务回滚，恰好一成一败，无重复行
    day = "2026-11-21"
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        return client.post("receives", json={
            "receive_no": "RC-DUP", "supplier_code": "SUP-A", "sku": "SKU-D",
            "dock_day": day, "qty": 2, "occurred_at": "2026-11-21T09:00:00Z"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: go(), range(2)))

    assert sorted(codes) == [200, 400]
    conn = connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM receive_lines WHERE receive_no='RC-DUP'").fetchone()["c"]
        accepted = conn.execute(
            "SELECT COUNT(*) c FROM ingest_events WHERE line_no='RC-DUP' AND result='accepted'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 1 and accepted == 1

# --- 6. 输入口径：非法日/非法时间戳被拒 ----------------------------------------

def test_invalid_day_and_timestamp_rejected(client):
    r = client.post("receives", json={
        "receive_no": "RC-BAD1", "supplier_code": "SUP-A", "sku": "SKU-B",
        "dock_day": "2026-13-40", "qty": 1})
    assert r.status_code == 400
    r = client.post("receives", json={
        "receive_no": "RC-BAD2", "supplier_code": "SUP-A", "sku": "SKU-B",
        "dock_day": DAY, "qty": 1, "occurred_at": "not-a-timestamp"})
    assert r.status_code == 400
    r = client.post("settings/dock-day", json={"dock_day": "11/18/2026"})
    assert r.status_code == 400

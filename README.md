# Tridock（到货三联台）

骨架：到货预报装载 → 实收入账 → 供应商回执装载 → 三方键对齐与差额处置 → 到货日结案封印。

- 前端 http://127.0.0.1:5900
- API http://127.0.0.1:10900

## 日闭包架构（全局唯一口径：申报 dock_day 入湖）

三条装载腿（预报/实收/回执）一律**按请求体申报的 `dock_day` 落入正式表日闭包**：

- 实收的 `occurred_at` 仅作审计列原样留存，**不参与折日**，不能把行挪到别的日历日，也不能改挂下一开放日。
- 三腿共用同一把封印闸（`app/services/gate.py`）：日闭包一旦 `sealed`，任何一腿写入整笔拒绝。
- 每次装载在 `BEGIN IMMEDIATE` 事务内完成「封印复查 → 正式行写入 → 快照落库」，三者同提交；
  封印拒绝时正式表零写入，只留 `rejected_sealed` 快照事件，不存在双写半截。
- 对齐扫描、差额分桶、结案闸、总览日计数、按日列表全部只认正式表 `dock_day` 一列，
  与按日列表同列同源，不存在第二套日历，禁止只改列表展示日。
- 批次快照表 `ingest_events` 固定记录 `fold_rule=declared_dock_day`、`declared_dock_day`、
  `closed_dock_day`、`occurred_at`、`folded_count`（本架构恒为 0）与受理/拒绝结果，可逐笔核对。

口径的唯一代码出处是 `app/services/daykey.py`，任何模块不得自行解析 `occurred_at` 决定落日。

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10900 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5900
```

## 核对测试

```bash
cd backend
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests/ -v
```

覆盖：occurred_at 与申报日不同时三腿仍落申报日；封印日三腿写入全拒且正式表零写入；
跨日 occurred_at 不能撬开封印日或改挂；总览与按日列表同日计数一致；对齐键集合跨日隔离；
并发装载与封印交错不产生半截写入；同单号并发恰好一成一败；非法日/时间戳拒绝。

测试库路径由环境变量 `TRIDOCK_DATA_DIR` 覆盖，不污染运行库。

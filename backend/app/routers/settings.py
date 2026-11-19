from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import connect
from app.services.daykey import DayKeyError, validate_dock_day

router = APIRouter(tags=["settings"])

class DayIn(BaseModel):
    dock_day: str

@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()

@router.post("/settings/dock-day")
def set_dock_day(body: DayIn):
    try:
        day = validate_dock_day(body.dock_day)
    except DayKeyError as e:
        raise HTTPException(400, str(e)) from e
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('dock_day', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (day,),
        )
        conn.commit()
        return {"dock_day": day}
    finally:
        conn.close()

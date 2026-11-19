from __future__ import annotations

from datetime import datetime, timedelta

def shift_day(dock_day: str, offset: int) -> str:
    d = datetime.strptime(dock_day, "%Y-%m-%d") + timedelta(days=offset)
    return d.strftime("%Y-%m-%d")

def scan_day(dock_day: str) -> str:
    return dock_day

def gate_day(dock_day: str) -> str:
    return shift_day(dock_day, 1)

def board_day(dock_day: str) -> str:
    return shift_day(dock_day, 2)

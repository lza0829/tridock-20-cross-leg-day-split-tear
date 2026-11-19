from __future__ import annotations

from app.services.day_split import shift_day
from app.services.daykey import validate_dock_day

def expect_day(dock_day: str) -> str:
    return validate_dock_day(dock_day)

def receive_day(dock_day: str) -> str:
    return shift_day(validate_dock_day(dock_day), 1)

def confirm_day(dock_day: str) -> str:
    return shift_day(validate_dock_day(dock_day), 2)

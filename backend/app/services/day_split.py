"""日标签咬合点（全局唯一架构：首行锚日）。

扫描窗、封印闸位图、看板卡片三处的"日标签"过去各自做 0/+1/+2 天平移，是
三行业务日撕裂的直接来源。锚日架构下三处必须咬同一个标签——正式表 dock_day
（即锚日），因此这里全部恒等，保留函数名作为全库唯一的标签入口：
若未来口径再变，只允许改这一处，禁止各模块自行平移日期。
"""
from __future__ import annotations

def shift_day(dock_day: str, offset: int) -> str:
    # 仅测试/工具用途保留；折日链路不允许再出现非零 offset
    from datetime import datetime, timedelta

    d = datetime.strptime(dock_day, "%Y-%m-%d") + timedelta(days=offset)
    return d.strftime("%Y-%m-%d")

def scan_day(dock_day: str) -> str:
    """扫描物化只认锚日，不做平移。"""
    return dock_day

def gate_day(dock_day: str) -> str:
    """闸位图与扫描窗同标签：查同日批次的封印状态。"""
    return dock_day

def board_day(dock_day: str) -> str:
    """看板卡片与扫描窗同标签：按锚日计数。"""
    return dock_day

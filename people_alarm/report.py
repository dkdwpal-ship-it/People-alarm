"""기간별 업무 리스트를 계산하고 마크다운으로 렌더링."""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Literal

from .models import Task
from .schedule import occurrences
from .store import Store

Period = Literal["today", "week", "month", "next-month"]
WEEKDAYS = "월화수목금토일"
OVERDUE_LOOKBACK_DAYS = 14


@dataclass
class Item:
    task: Task
    due: dt.date
    done: bool

    @property
    def prep_start(self) -> dt.date:
        return self.due - dt.timedelta(days=self.task.lead_days)


def period_range(period: Period, today: dt.date) -> tuple[dt.date, dt.date]:
    if period == "today":
        return today, today
    if period == "week":
        start = today - dt.timedelta(days=today.weekday())
        return start, start + dt.timedelta(days=6)
    if period == "month":
        y, m = today.year, today.month
    else:  # next-month
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])


def _items(store: Store, start: dt.date, end: dt.date) -> list[Item]:
    items = [
        Item(t, d, store.is_done(t.id, d.isoformat()))
        for t in store.tasks
        for d in occurrences(t.schedule, start, end)
    ]
    return sorted(items, key=lambda i: (i.due, i.task.category, i.task.title))


@dataclass
class Report:
    period: Period
    today: dt.date
    start: dt.date
    end: dt.date
    due: list[Item]  # 기간 안에 마감
    upcoming: list[Item]  # 기간 이후 마감이지만 지금 준비를 시작해야 함
    overdue: list[Item]  # 지난 마감 중 미완료


def build_report(store: Store, period: Period, today: dt.date) -> Report:
    start, end = period_range(period, today)
    max_lead = max((t.lead_days for t in store.tasks), default=0)
    upcoming = [
        i
        for i in _items(store, end + dt.timedelta(days=1), end + dt.timedelta(days=max_lead))
        if i.prep_start <= end and not i.done
    ]
    due = _items(store, start, end)
    # 반복 업무는 가장 최근에 놓친 회차 하나만, 이번 기간에 다시 나오는 업무는 제외
    in_period = {i.task.id for i in due}
    latest_missed: dict[str, Item] = {}
    for i in _items(store, today - dt.timedelta(days=OVERDUE_LOOKBACK_DAYS), today - dt.timedelta(days=1)):
        if not i.done and i.due < start and i.task.id not in in_period:
            latest_missed[i.task.id] = i
    overdue = sorted(latest_missed.values(), key=lambda i: i.due)
    return Report(period, today, start, end, due, upcoming, overdue)


def _fmt_date(d: dt.date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def _dday(due: dt.date, today: dt.date) -> str:
    diff = (due - today).days
    return "D-DAY" if diff == 0 else (f"D-{diff}" if diff > 0 else f"D+{-diff}")


def _line(i: Item, today: dt.date) -> str:
    t = i.task
    box = "[x]" if i.done else "[ ]"
    owner = f" · 담당: {t.owner}" if t.owner else ""
    return (
        f"- {box} **{t.title}** `{t.category}` {_fmt_date(i.due)} {_dday(i.due, today)}{owner}\n"
        f"  - {t.description}\n"
        f"  - 출처: {t.source} — \"{t.evidence}\" (id: `{t.id}`)"
    )


TITLES = {"today": "오늘", "week": "이번 주", "month": "이번 달", "next-month": "다음 달"}


def render_markdown(r: Report) -> str:
    span = _fmt_date(r.start) if r.start == r.end else f"{_fmt_date(r.start)} ~ {_fmt_date(r.end)}"
    out = [f"# {TITLES[r.period]} 업무 리스트 ({r.start.year}년 {span})", ""]
    out.append(f"기준일: {r.today.isoformat()} · 마감 {len(r.due)}건 · 준비 시작 {len(r.upcoming)}건 · 지연 {len(r.overdue)}건")
    out.append("")
    if r.overdue:
        out += ["## ⚠️ 지난 마감 (미완료)", ""] + [_line(i, r.today) for i in r.overdue] + [""]
    out += [f"## 📅 {TITLES[r.period]} 마감 업무", ""]
    out += [_line(i, r.today) for i in r.due] or ["- 해당 기간에 마감되는 업무가 없습니다."]
    out.append("")
    if r.upcoming:
        out += ["## 🔜 지금 준비를 시작할 업무", ""]
        out += [
            _line(i, r.today) + f"\n  - 준비 시작일: {_fmt_date(i.prep_start)}" for i in r.upcoming
        ]
        out.append("")
    return "\n".join(out)

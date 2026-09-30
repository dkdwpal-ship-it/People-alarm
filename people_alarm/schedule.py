"""반복 규칙(Schedule)으로 실제 날짜를 계산. LLM 없이 결정적으로 동작한다."""

from __future__ import annotations

import calendar
import datetime as dt
from functools import lru_cache
from typing import Iterator

from .models import Schedule

# 휴일 이동 때문에 기간 밖의 날짜가 안으로 들어올 수 있어 넉넉히 계산한다.
_MARGIN = dt.timedelta(days=10)


@lru_cache(maxsize=None)
def _kr_holidays(year: int) -> frozenset[dt.date]:
    try:
        import holidays
    except ImportError:  # holidays 미설치 시 주말만 고려
        return frozenset()
    return frozenset(holidays.KR(years=year).keys())


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in _kr_holidays(d.year)


def adjust_for_holiday(d: dt.date, rule: str) -> dt.date:
    if rule == "none":
        return d
    step = dt.timedelta(days=-1 if rule == "before" else 1)
    while not is_business_day(d):
        d += step
    return d


def _month_day(year: int, month: int, day: int) -> dt.date:
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, last if day == -1 else min(day, last))


def _nth_weekday(year: int, month: int, nth: int, weekday: int) -> dt.date | None:
    days = [
        dt.date(year, month, d)
        for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if dt.date(year, month, d).weekday() == weekday
    ]
    if nth == -1:
        return days[-1]
    return days[nth - 1] if 1 <= nth <= len(days) else None


def _months_between(start: dt.date, end: dt.date) -> Iterator[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _raw_dates(s: Schedule, start: dt.date, end: dt.date) -> Iterator[dt.date]:
    if s.frequency == "once":
        if s.date:
            yield dt.date.fromisoformat(s.date)
        return
    if s.frequency == "weekly":
        if s.weekday is None:
            return
        d = start + dt.timedelta(days=(s.weekday - start.weekday()) % 7)
        while d <= end:
            yield d
            d += dt.timedelta(days=7)
        return
    # monthly / yearly
    months = set(s.months) if s.months else (set(range(1, 13)) if s.frequency == "monthly" else set())
    for y, m in _months_between(start, end):
        if m not in months:
            continue
        if s.nth is not None and s.weekday is not None:
            d = _nth_weekday(y, m, s.nth, s.weekday)
            if d:
                yield d
        elif s.day is not None:
            yield _month_day(y, m, s.day)


def occurrences(s: Schedule, start: dt.date, end: dt.date) -> list[dt.date]:
    """[start, end] 구간에 들어오는 (휴일 조정 후) 기준일 목록."""
    result = {
        adjust_for_holiday(d, s.holiday_rule)
        for d in _raw_dates(s, start - _MARGIN, end + _MARGIN)
    }
    return sorted(d for d in result if start <= d <= end)

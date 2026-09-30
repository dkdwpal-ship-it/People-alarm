import datetime as dt

from people_alarm.models import Schedule
from people_alarm.schedule import adjust_for_holiday, occurrences

D = dt.date


def test_monthly_day_and_last_day():
    s = Schedule(frequency="monthly", day=10)
    assert occurrences(s, D(2026, 1, 1), D(2026, 3, 31)) == [D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10)]
    last = Schedule(frequency="monthly", day=-1)
    assert occurrences(last, D(2026, 2, 1), D(2026, 2, 28)) == [D(2026, 2, 28)]


def test_day_31_clamps_to_month_end():
    s = Schedule(frequency="monthly", day=31)
    assert occurrences(s, D(2026, 4, 1), D(2026, 4, 30)) == [D(2026, 4, 30)]


def test_yearly_quarterly():
    s = Schedule(frequency="yearly", months=[3, 6, 9, 12], day=-1)
    assert occurrences(s, D(2026, 1, 1), D(2026, 12, 31)) == [
        D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30), D(2026, 12, 31)
    ]


def test_weekly():
    s = Schedule(frequency="weekly", weekday=0)  # 월요일
    assert occurrences(s, D(2026, 9, 28), D(2026, 10, 11)) == [D(2026, 9, 28), D(2026, 10, 5)]


def test_nth_weekday():
    s = Schedule(frequency="monthly", nth=2, weekday=1)  # 둘째 주 화요일
    assert occurrences(s, D(2026, 10, 1), D(2026, 10, 31)) == [D(2026, 10, 13)]
    last_fri = Schedule(frequency="monthly", nth=-1, weekday=4)
    assert occurrences(last_fri, D(2026, 10, 1), D(2026, 10, 31)) == [D(2026, 10, 30)]


def test_once():
    s = Schedule(frequency="once", date="2026-11-03")
    assert occurrences(s, D(2026, 11, 1), D(2026, 11, 30)) == [D(2026, 11, 3)]
    assert occurrences(s, D(2026, 12, 1), D(2026, 12, 31)) == []


def test_holiday_adjustment():
    # 2026-10-10은 토요일 → after는 10/12(월), before는 10/9(금, 한글날) 건너뛰고 10/8(목)
    assert adjust_for_holiday(D(2026, 10, 10), "after") == D(2026, 10, 12)
    assert adjust_for_holiday(D(2026, 10, 10), "before") == D(2026, 10, 8)
    assert adjust_for_holiday(D(2026, 10, 10), "none") == D(2026, 10, 10)


def test_holiday_shift_across_window_boundary():
    # 1/31(토)가 before 규칙으로 1/30(금)로 당겨짐 → 1월 범위 안에만 포함
    s = Schedule(frequency="monthly", day=-1, holiday_rule="before")
    assert D(2026, 1, 30) in occurrences(s, D(2026, 1, 1), D(2026, 1, 31))
    # after 규칙: 1/31(토)→2/2(월), 2/28(토)→3/3(화, 3/2는 삼일절 대체공휴일)로 밀려 다음 달에 잡힘
    s2 = Schedule(frequency="monthly", day=-1, holiday_rule="after")
    assert occurrences(s2, D(2026, 2, 1), D(2026, 2, 28)) == [D(2026, 2, 2)]
    assert D(2026, 3, 3) in occurrences(s2, D(2026, 3, 1), D(2026, 3, 31))

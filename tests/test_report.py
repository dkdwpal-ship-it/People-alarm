import datetime as dt

from people_alarm.models import ExtractedTask, Schedule, Task
from people_alarm.report import build_report, period_range, render_markdown
from people_alarm.store import Store

D = dt.date


def _task(title, schedule, lead_days=3):
    return Task.from_extracted(
        ExtractedTask(
            title=title, description="설명", category="급여", owner=None,
            schedule=schedule, lead_days=lead_days, evidence="근거",
        ),
        source="매뉴얼.docx",
    )


def _store(tmp_path, tasks):
    store = Store(tmp_path)
    store._data["tasks"] = [t.model_dump() for t in tasks]
    return store


def test_period_range():
    today = D(2026, 9, 30)  # 수요일
    assert period_range("today", today) == (today, today)
    assert period_range("week", today) == (D(2026, 9, 28), D(2026, 10, 4))
    assert period_range("month", today) == (D(2026, 9, 1), D(2026, 9, 30))
    assert period_range("next-month", D(2026, 12, 5)) == (D(2027, 1, 1), D(2027, 1, 31))


def test_report_sections(tmp_path):
    payroll = _task("급여 지급", Schedule(frequency="monthly", day=25))
    tax = _task("원천세 신고", Schedule(frequency="monthly", day=10, holiday_rule="after"), lead_days=7)
    store = _store(tmp_path, [payroll, tax])

    r = build_report(store, "week", D(2026, 10, 5))  # 10/5~10/11
    # 원천세 10/10(토) → 10/12(월)로 밀려 이번 주 마감이 아니라 '준비 시작'에 들어간다
    assert [i.task.title for i in r.due] == []
    assert [(i.task.title, i.due) for i in r.upcoming] == [("원천세 신고", D(2026, 10, 12))]
    # 9/25 급여는 완료 안 됐으므로 지연으로 표시
    assert [(i.task.title, i.due) for i in r.overdue] == [("급여 지급", D(2026, 9, 25))]

    store.mark_done(payroll.id, "2026-09-25")
    r2 = build_report(store, "week", D(2026, 10, 5))
    assert r2.overdue == []

    md = render_markdown(r)
    assert "원천세 신고" in md and "준비 시작" in md and "D+10" in md

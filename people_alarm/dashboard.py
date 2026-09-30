"""웹 대시보드용 데이터 묶음(payload)과 HTML 페이지 생성."""

from __future__ import annotations

import calendar
import datetime as dt
import json
from importlib import resources
from typing import Literal

from .schedule import describe, holidays_between, occurrences
from .store import Store

Mode = Literal["server", "static"]
OVERDUE_LOOKBACK_DAYS = 14
_PLACEHOLDER = "__PEOPLE_ALARM_DATA__"


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def data_window(today: dt.date) -> tuple[dt.date, dt.date]:
    """대시보드가 한 번에 들고 있는 날짜 범위: 3달 전 ~ 24달 후."""
    start = _add_months(today, -3)
    last = _add_months(today, 24)
    return start, dt.date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])


def build_payload(store: Store, today: dt.date, mode: Mode = "server", label: str = "") -> dict:
    start, end = data_window(today)
    tasks = store.tasks
    return {
        "mode": mode,
        "label": label,
        "today": today.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "overdueLookbackDays": OVERDUE_LOOKBACK_DAYS,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "category": t.category,
                "owner": t.owner,
                "leadDays": t.lead_days,
                "source": t.source,
                "evidence": t.evidence,
                "rule": describe(t.schedule),
            }
            for t in tasks
        ],
        "occurrences": sorted(
            ({"taskId": t.id, "due": d.isoformat()} for t in tasks for d in occurrences(t.schedule, start, end)),
            key=lambda o: (o["due"], o["taskId"]),
        ),
        "done": store.done_keys,
        "holidays": holidays_between(start, end),
    }


def render_page(payload: dict, standalone: bool = True) -> str:
    """대시보드 HTML. standalone=False면 문서 골격 없이 본문 조각만 반환."""
    template = resources.files("people_alarm").joinpath("web/dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    body = template.replace(_PLACEHOLDER, data)
    if not standalone:
        return body
    return (
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )

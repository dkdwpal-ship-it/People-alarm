import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from people_alarm.dashboard import build_payload, render_page
from people_alarm.models import ExtractedTask, Schedule, Task
from people_alarm.schedule import describe
from people_alarm.server import make_handler
from people_alarm.store import Store

D = dt.date


def _seed(tmp_path):
    task = Task.from_extracted(
        ExtractedTask(title="급여 지급 </script>", description="d", category="급여", owner=None,
                      schedule=Schedule(frequency="monthly", day=25, holiday_rule="before"),
                      lead_days=3, evidence="e"),
        source="a.md",
    )
    store = Store(tmp_path)
    store._data["tasks"] = [task.model_dump()]
    store.save()
    return task


def test_describe():
    assert describe(Schedule(frequency="monthly", day=25, holiday_rule="before")) == "매월 25일 · 휴일이면 전 영업일"
    assert describe(Schedule(frequency="yearly", months=[3, 6, 9, 12], day=-1)) == "매년 3·6·9·12월 말일"
    assert describe(Schedule(frequency="weekly", weekday=0)) == "매주 월요일"
    assert describe(Schedule(frequency="monthly", nth=2, weekday=1)) == "매월 둘째 주 화요일"


def test_payload_and_page(tmp_path):
    task = _seed(tmp_path)
    payload = build_payload(Store(tmp_path), D(2026, 9, 30), mode="static")
    assert payload["window"] == {"start": "2026-06-01", "end": "2028-09-30"}
    assert {"taskId": task.id, "due": "2026-09-23"} in payload["occurrences"]  # 추석 연휴 → 전 영업일
    assert payload["holidays"]["2026-10-09"] == "한글날"
    html = render_page(payload)
    assert html.startswith("<!doctype html>") and "__PEOPLE_ALARM_DATA__" not in html
    assert "급여 지급 <\\/script>" in html  # JSON 안의 </script>가 페이지를 깨지 않음
    assert "<!doctype" not in render_page(payload, standalone=False)


@pytest.fixture
def server(tmp_path):
    task = _seed(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, D(2026, 9, 30)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", task, tmp_path
    httpd.shutdown()


def _post(url, body, content_type="application/json"):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": content_type}, method="POST")
    return urllib.request.urlopen(req)


def test_server_routes(server):
    base, task, data_dir = server
    assert "업무 달력" in urllib.request.urlopen(base + "/").read().decode()
    payload = json.load(urllib.request.urlopen(base + "/api/dashboard?date=2027-01-15"))
    assert payload["today"] == "2027-01-15" and payload["mode"] == "server"

    res = json.load(_post(base + "/api/done", {"taskId": task.id, "due": "2026-09-23", "done": True}))
    assert res["done"] == [f"{task.id}@2026-09-23"]
    assert Store(data_dir).is_done(task.id, "2026-09-23")
    res = json.load(_post(base + "/api/done", {"taskId": task.id, "due": "2026-09-23", "done": False}))
    assert res["done"] == []

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/api/done", {"taskId": task.id, "due": "2026-09-23", "done": True}, "text/plain")
    assert e.value.code == 415
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/api/done", {"taskId": "nope", "due": "2026-09-23", "done": True})
    assert e.value.code == 404

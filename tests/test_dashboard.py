import datetime as dt
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import time
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


def fake_extract(path, today):
    if "broken" in path.name:
        raise ValueError("문서에서 읽을 수 있는 글자가 없습니다.")
    return [Task.from_extracted(
        ExtractedTask(title=f"{path.stem} 업무", description="d", category="세무", owner=None,
                      schedule=Schedule(frequency="monthly", day=10), lead_days=2, evidence="e"),
        source=path.name,
    )]


@pytest.fixture
def server(tmp_path):
    task = _seed(tmp_path)
    handler = make_handler(tmp_path, D(2026, 9, 30), docs_dir=tmp_path / "docs", extract=fake_extract)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", task, tmp_path
    httpd.shutdown()


def _upload(url, name, data):
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/octet-stream", "X-Filename": urllib.parse.quote(name)})
    return urllib.request.urlopen(req)


def _wait_docs(base):
    for _ in range(100):
        body = json.load(urllib.request.urlopen(base + "/api/documents"))
        if not body["busy"]:
            return {d["name"]: d for d in body["documents"]}
        time.sleep(0.05)
    raise AssertionError("분석이 끝나지 않음")


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


def test_upload_and_analyze(server):
    base, _, data_dir = server
    res = _upload(base + "/api/upload", "../../원천세 가이드.md", "매월 10일 원천세".encode())
    assert res.status == 202 and json.load(res)["name"] == "원천세 가이드.md"
    assert (data_dir / "docs" / "원천세 가이드.md").exists()  # 경로 조작 없이 docs 안에 저장

    docs = _wait_docs(base)
    doc = docs["원천세 가이드.md"]
    assert doc["job"]["status"] == "done" and doc["analyzed"] and doc["taskCount"] == 1
    titles = [t["title"] for t in json.load(urllib.request.urlopen(base + "/api/dashboard"))["tasks"]]
    assert "원천세 가이드 업무" in titles and "급여 지급 </script>" in titles  # 기존 업무 유지

    _upload(base + "/api/upload", "broken.txt", b"x")
    doc = _wait_docs(base)["broken.txt"]
    assert doc["job"]["status"] == "error" and "글자가 없습니다" in doc["job"]["error"]

    res = _post(base + "/api/analyze", {"name": "원천세 가이드.md"})
    assert res.status == 202
    assert _wait_docs(base)["원천세 가이드.md"]["taskCount"] == 1  # 다시 분석해도 중복 없음


def test_upload_rejects_bad_files(server):
    base, _, _ = server
    for name, data, code in [("a.hwp", b"x", 400), (".hidden.md", b"x", 400), ("empty.md", b"", 400)]:
        with pytest.raises(urllib.error.HTTPError) as e:
            _upload(base + "/api/upload", name, data)
        assert e.value.code == code, name
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/api/analyze", {"name": "없는문서.md"})
    assert e.value.code == 404


def test_upload_real_docx_and_reject_drm(server, tmp_path):
    import docx

    base, _, data_dir = server
    src = tmp_path / "src.docx"
    d = docx.Document(); d.add_paragraph("매월 10일 원천세 신고"); d.save(src)
    raw = src.read_bytes()
    assert _upload(base + "/api/upload", "세무 매뉴얼.docx", raw).status == 202
    assert (data_dir / "docs" / "세무 매뉴얼.docx").read_bytes() == raw  # 업로드 중 내용 손상 없음
    assert _wait_docs(base)["세무 매뉴얼.docx"]["job"]["status"] == "done"

    with pytest.raises(urllib.error.HTTPError) as e:
        _upload(base + "/api/upload", "보안문서.docx", b"<## DRM ##>" + b"\0" * 100)
    assert e.value.code == 400 and "DRM" in json.load(e.value)["error"]
    assert not (data_dir / "docs" / "보안문서.docx").exists()  # 거절된 파일은 저장하지 않음


def test_documents_reports_llm(server):
    base, _, _ = server
    body = json.load(urllib.request.urlopen(base + "/api/documents"))
    assert body["llm"]["model"] == "thinkingcap"
    from people_alarm import __version__
    assert body["version"] == __version__


def test_exclude_include_and_delete_documents(server):
    base, seeded, data_dir = server
    _upload(base + "/api/upload", "세무.md", "매월 10일 원천세".encode())
    _wait_docs(base)

    # 제외: 일정에서 빠지지만 분석 결과는 보관
    res = json.load(_post(base + "/api/documents/exclude", {"name": "세무.md", "excluded": True}))
    assert res == {"name": "세무.md", "excluded": True}
    payload = json.load(urllib.request.urlopen(base + "/api/dashboard"))
    assert all(t["source"] != "세무.md" for t in payload["tasks"])
    assert [t["title"] for t in payload["excludedTasks"]] == ["세무 업무"]
    assert payload["excludedDocuments"] == ["세무.md"]
    assert all(o["taskId"] != payload["excludedTasks"][0]["id"] for o in payload["occurrences"])
    doc = _wait_docs(base)["세무.md"]
    assert doc["excluded"] and doc["taskCount"] == 1

    # 다시 분석해도 제외 상태 유지
    _post(base + "/api/analyze", {"name": "세무.md"})
    assert _wait_docs(base)["세무.md"]["excluded"]

    # 다시 포함
    _post(base + "/api/documents/exclude", {"name": "세무.md", "excluded": False})
    payload = json.load(urllib.request.urlopen(base + "/api/dashboard"))
    assert "세무 업무" in [t["title"] for t in payload["tasks"]] and payload["excludedTasks"] == []

    # 삭제: 파일과 업무 모두 제거, 다른 문서 업무는 유지
    res = json.load(_post(base + "/api/documents/delete", {"name": "세무.md"}))
    assert res["removedTasks"] == 1
    assert not (data_dir / "docs" / "세무.md").exists()
    titles = [t["title"] for t in json.load(urllib.request.urlopen(base + "/api/dashboard"))["tasks"]]
    assert titles == [seeded.title]
    assert "세무.md" not in _wait_docs(base)

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/api/documents/delete", {"name": "없는문서.md"})
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/api/documents/exclude", {"name": "../tasks.json"})
    assert e.value.code == 404

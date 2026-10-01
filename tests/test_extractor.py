import datetime as dt
import json

import pytest

from fake_vllm import FakeVLLM
from people_alarm.config import LLMConfig
from people_alarm.extractor import LLMOutputError, extract_tasks, parse_tasks, split_text

TASK = {
    "title": "급여 지급", "description": "급여 이체", "category": "급여", "owner": "인사팀",
    "lead_days": 3, "evidence": "매월 25일 급여 지급",
    "schedule": {"frequency": "monthly", "date": None, "months": [], "day": 25,
                 "weekday": None, "nth": None, "holiday_rule": "before"},
}


def _doc(tmp_path, text="매월 25일 급여 지급"):
    p = tmp_path / "guide.md"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def fake():
    servers = []

    def start(reply, **kw):
        s = FakeVLLM(reply, **kw)
        servers.append(s)
        return s, LLMConfig(base_url=s.url, model=s.model, timeout=10)

    yield start
    for s in servers:
        s.close()


def test_structured_request_to_vllm(tmp_path, fake):
    server, cfg = fake(lambda m, r: (json.dumps({"tasks": [TASK]}, ensure_ascii=False), "stop"))
    tasks = extract_tasks(_doc(tmp_path), dt.date(2026, 10, 1), config=cfg)

    req = server.requests[0]
    assert req["model"] == "thinkingcap" and req["temperature"] == 0
    assert req["response_format"]["type"] == "json_schema"
    assert "$ref" not in json.dumps(req["response_format"])  # 스키마를 펼쳐서 보냄
    assert "기준일: 2026-10-01" in req["messages"][1]["content"]
    assert [t.title for t in tasks] == ["급여 지급"] and tasks[0].source == "guide.md"


def test_falls_back_when_server_rejects_json_schema(tmp_path, fake):
    reply = lambda m, r: ("<think>문서를 보니 급여일이...</think>\n```json\n" + json.dumps({"tasks": [TASK]}) + "\n```", "stop")
    server, cfg = fake(reply, reject_schema=True)
    tasks = extract_tasks(_doc(tmp_path), dt.date(2026, 10, 1), config=cfg)
    assert len(tasks) == 1
    assert "response_format" in server.requests[0] and "response_format" not in server.requests[1]


def test_retries_once_on_broken_json(tmp_path, fake):
    answers = iter(["업무는 급여 지급입니다.", json.dumps({"tasks": [TASK]})])
    server, cfg = fake(lambda m, r: (next(answers), "stop"))
    assert len(extract_tasks(_doc(tmp_path), dt.date(2026, 10, 1), config=cfg)) == 1
    assert server.requests[1]["messages"][-1]["role"] == "user"  # 형식 재요청


def test_long_document_is_chunked_and_deduplicated(tmp_path, fake):
    server, cfg = fake(lambda m, r: (json.dumps({"tasks": [TASK]}), "stop"))
    text = "\n\n".join(f"{i}번 문단 " + "가" * 900 for i in range(10))
    cfg = LLMConfig(base_url=cfg.base_url, model=cfg.model, timeout=10, chunk_chars=3000)
    tasks = extract_tasks(_doc(tmp_path, text), dt.date(2026, 10, 1), config=cfg)
    assert len(server.requests) >= 3
    assert "번째 부분" in server.requests[0]["messages"][1]["content"]
    assert len(tasks) == 1  # 조각마다 같은 업무가 나와도 하나로 합침


def test_truncated_response_splits_chunk(tmp_path, fake):
    def reply(messages, req):
        doc = messages[1]["content"]
        return (json.dumps({"tasks": [TASK]}), "length" if len(doc) > 3000 else "stop")

    server, cfg = fake(reply)
    text = "\n\n".join("가" * 900 for _ in range(4))
    assert len(extract_tasks(_doc(tmp_path, text), dt.date(2026, 10, 1), config=cfg)) == 1
    assert len(server.requests) == 3  # 잘린 1회 + 반씩 2회


def test_parse_tasks_skips_invalid_rules():
    bad = dict(TASK, schedule={"frequency": "weekly"})  # weekday 없음
    worse = dict(TASK, schedule={"frequency": "monthly", "day": 40})
    good = dict(TASK, title="원천세")
    tasks = parse_tasks(json.dumps({"tasks": [bad, worse, good]}))
    assert [t.title for t in tasks] == ["원천세"]
    assert parse_tasks("생각 중... </think> {\"tasks\": []}") == []
    with pytest.raises(LLMOutputError):
        parse_tasks("죄송합니다")


def test_split_text():
    text = "a" * 50 + "\n\n" + "b" * 50 + "\n\n" + "c" * 50
    chunks = split_text(text, 110)
    assert len(chunks) == 2 and "".join(chunks) == text
    assert all(len(c) <= 200 for c in split_text("x" * 500, 200))

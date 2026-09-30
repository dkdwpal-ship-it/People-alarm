import datetime as dt
from types import SimpleNamespace

from people_alarm.extractor import MODEL, extract_tasks
from people_alarm.models import ExtractedTask, ExtractionResult, Schedule


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=self.result)


def test_extract_tasks_builds_request_and_tasks(tmp_path):
    doc = tmp_path / "guide.md"
    doc.write_text("매월 25일 급여 지급", encoding="utf-8")
    result = ExtractionResult(tasks=[
        ExtractedTask(title="급여 지급", description="급여 이체", category="급여", owner="인사팀",
                      schedule=Schedule(frequency="monthly", day=25, holiday_rule="before"),
                      lead_days=3, evidence="매월 25일 급여 지급"),
    ])
    messages = FakeMessages(result)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))

    tasks = extract_tasks(doc, dt.date(2026, 9, 30), client=client)

    assert messages.kwargs["model"] == MODEL
    assert messages.kwargs["output_format"] is ExtractionResult
    assert messages.kwargs["fallbacks"] == "default"
    assert "기준일: 2026-09-30" in messages.kwargs["messages"][0]["content"][-1]["text"]
    assert len(tasks) == 1 and tasks[0].source == "guide.md" and len(tasks[0].id) == 10

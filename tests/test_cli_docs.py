from people_alarm.cli import main
from people_alarm.models import ExtractedTask, Schedule, Task
from people_alarm.store import Store


def _seed(tmp_path):
    store = Store(tmp_path / "data")
    for src, title in (("급여.md", "급여 지급"), ("세무.md", "원천세 신고")):
        t = Task.from_extracted(ExtractedTask(
            title=title, description="d", category="c", owner="인사팀", lead_days=3, evidence="근거 문장",
            schedule=Schedule(frequency="monthly", day=10)), source=src)
        store._data["tasks"].append(t.model_dump())
        store._data["documents"][src] = {"sha256": "x"}
    store.save()
    return ["--data", str(tmp_path / "data")]


def test_exclude_include_and_list_all(tmp_path, capsys):
    base = _seed(tmp_path)
    docs = ["--docs", str(tmp_path / "docs")]
    assert main(base + ["exclude", "세무.md"] + docs) == 0
    assert Store(tmp_path / "data").is_excluded("세무.md")

    main(base + ["--date", "2026-10-01", "list"])
    out = capsys.readouterr().out
    assert "급여 지급" in out and "원천세 신고" not in out

    main(base + ["--date", "2026-10-01", "list", "--all", "-v"])
    out = capsys.readouterr().out
    assert "원천세 신고" in out and "[제외됨]" in out and "근거 문장" in out and "매월 10일" in out

    main(base + ["docs"] + docs)
    assert "[제외됨] 세무.md" in capsys.readouterr().out

    main(base + ["--date", "2026-10-01", "month"])
    assert "원천세" not in capsys.readouterr().out  # 리포트에서도 빠짐

    assert main(base + ["include", "세무.md"] + docs) == 0
    assert not Store(tmp_path / "data").is_excluded("세무.md")
    assert main(base + ["exclude", "없음.md"] + docs) == 1

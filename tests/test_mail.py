import datetime as dt
import json
import struct
import urllib.request
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from fake_vllm import FakeVLLM
from people_alarm import cli
from people_alarm.config import LLMConfig
from people_alarm.extractor import MAIL_GUIDE, extract_tasks
from people_alarm.loaders import check_format, extract_text, find_documents
from people_alarm.mail import (
    IMAPConfig, compose_eml, fetch_imap, html_to_text, mail_filename, msg_from_ole, parse_eml, save_mail,
)
from people_alarm.models import ExtractedTask, Schedule, Task
from people_alarm.store import Store

KST = dt.timezone(dt.timedelta(hours=9))
SENT = dt.datetime(2026, 10, 5, 9, 30, tzinfo=KST)  # 월요일

HTML = """<html><head><style>p{color:red}</style></head><body>
<p>안녕하세요, 인사팀입니다.</p><p>연말정산 서류를 <b>이번 주 금요일</b>까지 제출해 주세요.</p>
<table><tr><th>항목</th><th>기한</th></tr><tr><td>서류 제출</td><td>10/9(금)</td></tr></table>
</body></html>"""


def _intranet_mail(with_attachment=True) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "[인사팀] 연말정산 서류 제출 안내"
    msg["From"] = "인사팀 <noreply@intranet.example.co.kr>"
    msg["To"] = "all@example.co.kr"
    msg["Date"] = format_datetime(SENT)
    msg["Message-ID"] = "<abc123@intranet.example.co.kr>"
    msg.set_content("연말정산 서류를 이번 주 금요일까지 제출해 주세요.")
    msg.add_alternative(HTML, subtype="html")
    if with_attachment:
        msg.add_attachment("제출 서류: 소득공제 신고서".encode("utf-8"), maintype="text", subtype="plain",
                           filename="제출서류 목록.txt")
        msg.add_attachment(b"\x00\x01", maintype="application", subtype="octet-stream", filename="양식.hwp")
    return bytes(msg)


def test_html_to_text_keeps_tables_and_drops_styles():
    text = html_to_text(HTML)
    assert "color:red" not in text
    assert "이번 주 금요일까지 제출" in text
    assert "서류 제출 | 10/9(금)" in text


def test_parse_eml_and_text_for_llm(tmp_path):
    info = parse_eml(_intranet_mail())
    assert info.subject == "[인사팀] 연말정산 서류 제출 안내"
    assert info.sent_date == dt.date(2026, 10, 5)
    assert "서류 제출 | 10/9(금)" in info.body  # HTML 본문을 우선 사용
    assert [a.name for a in info.attachments] == ["제출서류 목록.txt", "양식.hwp"]

    p = tmp_path / "m.eml"
    p.write_bytes(_intranet_mail())
    text = extract_text(p)
    assert "제목: [인사팀] 연말정산 서류 제출 안내" in text
    assert "보낸 날짜: 2026-10-05 09:30 (월)" in text
    assert "[첨부: 제출서류 목록.txt]\n제출 서류: 소득공제 신고서" in text
    assert "[첨부: 양식.hwp — 읽지 않음]" in text


def test_eml_with_cp949_body():
    raw = (
        "Subject: =?euc-kr?B?vsiz5w==?=\r\nDate: Mon, 05 Oct 2026 09:30:00 +0900\r\n"
        "Content-Type: text/plain; charset=ks_c_5601-1987\r\n\r\n"
    ).encode() + "10월 9일까지 제출".encode("cp949")
    info = parse_eml(raw)
    assert "10월 9일까지 제출" in info.body


def test_check_format_for_mails():
    check_format("a.eml", _intranet_mail())
    with pytest.raises(ValueError, match="메일\\(.eml\\) 형식이 아닙니다"):
        check_format("a.eml", b"\x00\x01binary")
    with pytest.raises(ValueError, match="Outlook"):
        check_format("a.msg", b"not an ole file")


class FakeOle:
    """olefile.OleFileIO 흉내 (.msg 구조)."""

    def __init__(self, streams):
        self.streams = streams

    def exists(self, name):
        return name in self.streams

    def openstream(self, name):
        import io
        return io.BytesIO(self.streams[name])

    def listdir(self, streams=True, storages=False):
        return sorted({tuple(n.split("/")[:1]) for n in self.streams if "/" in n})


def test_msg_from_ole():
    u = lambda s: s.encode("utf-16-le")  # noqa: E731
    filetime = int((SENT - dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() * 10_000_000)
    props = b"\0" * 32 + struct.pack("<IIQ", (0x0039 << 16) | 0x0040, 0, filetime)
    ole = FakeOle({
        "__substg1.0_0037001F": u("[총무팀] 사무실 이전 안내"),
        "__substg1.0_0C1A001F": u("총무팀"),
        "__substg1.0_1000001F": u("10월 20일(화)까지 짐을 정리해 주세요."),
        "__properties_version1.0": props,
        "__attach_version1.0_#00000000/__substg1.0_3707001F": u("일정표.txt"),
        "__attach_version1.0_#00000000/__substg1.0_37010102": "10/20 짐 정리".encode("utf-8"),
    })
    info = msg_from_ole(ole)
    assert info.subject == "[총무팀] 사무실 이전 안내" and info.sender == "총무팀"
    assert info.sent_date == dt.date(2026, 10, 5)
    assert "짐을 정리" in info.body
    assert info.attachments[0].name == "일정표.txt"


def test_save_mail_is_idempotent(tmp_path):
    raw = _intranet_mail()
    p1, new1 = save_mail(raw, tmp_path / "mails")
    p2, new2 = save_mail(raw, tmp_path / "mails")
    assert new1 and not new2 and p1 == p2
    assert p1.name.startswith("20261005-0930_[인사팀] 연말정산 서류 제출 안내_") and p1.suffix == ".eml"
    assert find_documents(tmp_path) == [p1]  # docs/mails 하위 폴더도 ingest 대상
    assert "/" not in mail_filename(parse_eml(compose_eml("a/b:c", "x")), b"x")


def test_compose_eml_roundtrip():
    raw = compose_eml("주간회의", "매주 월요일 10시 주간회의", dt.datetime(2026, 10, 2), "기획팀")
    info = parse_eml(raw)
    assert info.subject == "주간회의" and info.sender == "기획팀"
    assert info.sent_date == dt.date(2026, 10, 2)
    assert info.body == "매주 월요일 10시 주간회의"


def test_mail_extraction_uses_sent_date(tmp_path):
    task = {"title": "연말정산 서류 제출", "description": "d", "category": "연말정산", "owner": "인사팀",
            "lead_days": 4, "evidence": "이번 주 금요일까지 제출",
            "schedule": {"frequency": "once", "date": "2026-10-09"}}
    server = FakeVLLM(lambda m, r: (json.dumps({"tasks": [task]}, ensure_ascii=False), "stop"))
    try:
        p = tmp_path / "m.eml"
        p.write_bytes(_intranet_mail())
        cfg = LLMConfig(base_url=server.url, model=server.model, timeout=10)
        tasks = extract_tasks(p, dt.date(2026, 10, 20), config=cfg)
    finally:
        server.close()
    sys_msg, user_msg = server.requests[0]["messages"]
    assert MAIL_GUIDE in sys_msg["content"]
    assert "기준일: 2026-10-05" in user_msg["content"]  # 오늘이 아니라 메일 보낸 날
    assert "서류 제출 | 10/9(금)" in user_msg["content"]
    assert server.auth_headers == [None]  # 사내 vLLM에 API 키 없이 호출
    assert tasks[0].schedule.date == "2026-10-09" and tasks[0].source == "m.eml"


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []
        self.literal = None

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(i).encode() for i in range(1, len(self.messages) + 1))]
        raw = self.messages[int(args[0]) - 1]
        return "OK", [(b"1 (UID 1 BODY[] {n}", raw), b")"]

    def logout(self):
        self.calls.append(("logout",))


def test_fetch_imap(tmp_path):
    imap = FakeIMAP([_intranet_mail(), _intranet_mail()])  # 같은 메일 두 통
    cfg = IMAPConfig(host="mail", user="me", password="pw", sender="noreply@intranet.example.co.kr")
    saved, skipped = fetch_imap(cfg, tmp_path, dt.date(2026, 9, 28), connect=lambda: imap)
    assert len(saved) == 1 and skipped == 1
    search = next(c for c in imap.calls if c[0] == "SEARCH")
    assert search[1:] == ("SINCE", "28-Sep-2026", "FROM", '"noreply@intranet.example.co.kr"')
    assert ("select", '"INBOX"', True) in imap.calls  # 읽기 전용으로 열어 메일 상태를 바꾸지 않음
    assert all("PEEK" in c[2] for c in imap.calls if c[0] == "FETCH")
    assert imap.calls[-1] == ("logout",)


def test_imap_config_requires_env(monkeypatch):
    for k in ("HOST", "USER", "PASSWORD"):
        monkeypatch.delenv(f"PEOPLE_ALARM_IMAP_{k}", raising=False)
    with pytest.raises(ValueError, match="PEOPLE_ALARM_IMAP_HOST"):
        IMAPConfig.from_env()


def _fake_extract(path, reference_date):
    return [Task.from_extracted(
        ExtractedTask(title="서류 제출", description="d", category="연말정산", owner=None,
                      schedule=Schedule(frequency="once", date="2026-10-09"), lead_days=3, evidence="e"),
        source=path.name,
    )]


def test_cli_add_mail(tmp_path, monkeypatch, capsys):
    import people_alarm.extractor as extractor
    monkeypatch.setattr(extractor, "extract_tasks", _fake_extract)
    src = tmp_path / "in.eml"
    src.write_bytes(_intranet_mail())
    args = ["--data", str(tmp_path / "data"), "--date", "2026-10-05", "add-mail", str(src), "--docs", str(tmp_path / "docs")]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "+ 저장" in out and "서류 제출  (마감: 2026-10-09)" in out
    assert [t.title for t in Store(tmp_path / "data").tasks] == ["서류 제출"]

    assert cli.main(args) == 0  # 같은 메일을 다시 넣으면 저장·분석하지 않음
    assert "= 이미 있는 메일" in capsys.readouterr().out
    assert len(list((tmp_path / "docs" / "mails").iterdir())) == 1


def test_server_paste_mail(tmp_path):
    from http.server import ThreadingHTTPServer
    import threading
    import time

    from people_alarm.server import make_handler

    handler = make_handler(tmp_path, dt.date(2026, 10, 5), docs_dir=tmp_path / "docs", extract=_fake_extract)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        body = {"subject": "[인사팀] 서류 제출", "sender": "인사팀", "sent": "2026-10-05", "body": "이번 주 금요일까지 제출"}
        req = urllib.request.Request(base + "/api/mail", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        res = urllib.request.urlopen(req)
        name = json.load(res)["name"]
        assert res.status == 202 and name.startswith("20261005-0000_[인사팀] 서류 제출_")
        assert (tmp_path / "docs" / "mails" / name).exists()
        for _ in range(100):
            docs = json.load(urllib.request.urlopen(base + "/api/documents"))
            if not docs["busy"]:
                break
            time.sleep(0.05)
        doc = next(d for d in docs["documents"] if d["name"] == name)
        assert doc["kind"] == "mail" and doc["job"]["status"] == "done" and doc["taskCount"] == 1

        # 하위 폴더(docs/mails)에 있는 메일도 다시 분석할 수 있다
        req = urllib.request.Request(base + "/api/analyze", data=json.dumps({"name": name}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req).status == 202

        bad = urllib.request.Request(base + "/api/mail", data=json.dumps({"body": " "}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        httpd.shutdown()

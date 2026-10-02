"""사내 인트라넷(그룹웨어) 메일 읽기·저장·가져오기.

- .eml: 그룹웨어의 "EML로 저장", 메일 원본 보기, IMAP으로 받은 메일 (표준 라이브러리로 해석)
- .msg: Outlook에서 메일을 끌어다 놓아 저장한 파일 (olefile로 해석)
- 붙여넣은 본문: compose_eml()로 .eml을 만들어 같은 방식으로 다룬다.

메일은 문서와 달리 "이번 주 금요일까지" 같은 상대 날짜가 많으므로,
추출할 때 메일 발송일을 기준일로 쓴다 (extractor.py 참고).
"""

from __future__ import annotations

import datetime as dt
import email
import email.policy
import hashlib
import imaplib
import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

MAIL_SUFFIXES = {".eml", ".msg"}
MAIL_DIRNAME = "mails"  # docs/ 아래 메일 저장 폴더
_KST = dt.timezone(dt.timedelta(hours=9))
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAX_ATTACHMENT_CHARS = 20000  # 첨부 하나에서 LLM에 보낼 최대 글자 수


@dataclass
class Attachment:
    name: str
    data: bytes


@dataclass
class MailInfo:
    subject: str = ""
    sender: str = ""
    to: str = ""
    sent: dt.datetime | None = None
    message_id: str = ""
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def sent_date(self) -> dt.date | None:
        """발송일(한국 시간 기준)."""
        if self.sent is None:
            return None
        s = self.sent if self.sent.tzinfo is None else self.sent.astimezone(_KST)
        return s.date()


# ---------------------------------------------------------------- HTML → 텍스트
class _HTMLText(HTMLParser):
    """그룹웨어 메일 HTML에서 글자만 뽑는다. 표는 '칸 | 칸' 줄로 바꾼다."""

    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            (self._row if self._row is not None else self.out).append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self.out.append("\n" + " | ".join(self._row) + "\n")
            self._row = None
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        (self._cell if self._cell is not None else self.out).append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.out).splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    p.close()
    return p.text()


# ---------------------------------------------------------------- .eml
def _decode_part(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):  # 잘못 적힌 charset → 한국어 인코딩으로 다시 시도
        raw = part.get_payload(decode=True) or b""
        for enc in ("utf-8", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


def parse_eml(raw: bytes) -> MailInfo:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sent = None
    if msg["Date"]:
        try:
            sent = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            sent = None
    plain: list[str] = []
    html: list[str] = []
    attachments: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        ctype = part.get_content_type()
        if filename or part.get_content_disposition() == "attachment":
            data = part.get_payload(decode=True) or b""
            if ctype == "message/rfc822" and not data:  # 메일에 첨부된 메일
                inner = part.get_payload()
                data = bytes(inner[0]) if isinstance(inner, list) and inner else b""
            attachments.append(Attachment(filename or "첨부", data))
        elif ctype == "text/plain":
            plain.append(_decode_part(part))
        elif ctype == "text/html":
            html.append(_decode_part(part))
    # 같은 내용의 text/plain과 text/html이 함께 오면 표 구조가 남는 HTML을 쓴다.
    body = "\n\n".join(html_to_text(h) for h in html) if html else "\n\n".join(plain)
    return MailInfo(
        subject=str(msg["Subject"] or "").strip(),
        sender=str(msg["From"] or "").strip(),
        to=str(msg["To"] or "").strip(),
        sent=sent,
        message_id=str(msg["Message-ID"] or "").strip(),
        body=body.strip(),
        attachments=attachments,
    )


# ---------------------------------------------------------------- .msg (Outlook)
def _filetime(value: int) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=value // 10)


def _msg_prop(ole, prefix: str, prop: str) -> str | bytes | None:
    """MAPI 속성 스트림 읽기. 문자열은 유니코드(001F) → ANSI(001E) 순서로 찾는다."""
    base = f"{prefix}__substg1.0_{prop}"
    for suffix, enc in (("001F", "utf-16-le"), ("001E", "cp949")):
        if ole.exists(base + suffix):
            return ole.openstream(base + suffix).read().decode(enc, errors="replace").rstrip("\0")
    if ole.exists(base + "0102"):
        return ole.openstream(base + "0102").read()
    return None


def _msg_sent(ole) -> dt.datetime | None:
    """__properties_version1.0에서 보낸 시각(PR_CLIENT_SUBMIT_TIME) 또는 받은 시각을 읽는다."""
    if not ole.exists("__properties_version1.0"):
        return None
    raw = ole.openstream("__properties_version1.0").read()
    found: dict[int, int] = {}
    for off in range(32, len(raw) - 15, 16):  # 최상위 메시지는 머리글 32바이트 뒤에 16바이트씩
        tag, _flags, value = struct.unpack_from("<IIQ", raw, off)
        if tag & 0xFFFF == 0x0040:  # PT_SYSTIME
            found[tag >> 16] = value
    for prop_id in (0x0039, 0x0E06):  # 보낸 시각, 받은 시각
        if found.get(prop_id):
            return _filetime(found[prop_id])
    return None


def msg_from_ole(ole) -> MailInfo:
    """olefile.OleFileIO(또는 같은 메서드를 가진 객체)에서 메일 정보를 읽는다."""
    body = _msg_prop(ole, "", "1000") or ""
    if not str(body).strip():
        html = _msg_prop(ole, "", "1013")
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")
        body = html_to_text(html or "")
    attachments: list[Attachment] = []
    storages = {e[0] for e in ole.listdir(streams=False, storages=True) if e and e[0].startswith("__attach_version1.0_")}
    for storage in sorted(storages):
        prefix = storage + "/"
        name = _msg_prop(ole, prefix, "3707") or _msg_prop(ole, prefix, "3704") or "첨부"
        data = _msg_prop(ole, prefix, "3701")
        if isinstance(data, bytes):
            attachments.append(Attachment(str(name), data))
    return MailInfo(
        subject=str(_msg_prop(ole, "", "0037") or "").strip(),
        sender=str(_msg_prop(ole, "", "0C1A") or _msg_prop(ole, "", "0C1F") or "").strip(),
        to=str(_msg_prop(ole, "", "0E04") or "").strip(),
        sent=_msg_sent(ole),
        message_id=str(_msg_prop(ole, "", "1035") or "").strip(),
        body=str(body).strip(),
        attachments=attachments,
    )


def parse_msg(path: Path) -> MailInfo:
    try:
        import olefile
    except ImportError as e:  # pragma: no cover - 의존성으로 설치됨
        raise ValueError("Outlook .msg 파일을 읽으려면 `pip install olefile`이 필요합니다.") from e
    if not path.read_bytes()[:8] == _OLE_MAGIC:
        raise ValueError("Outlook 메일(.msg) 형식이 아닙니다. 메일 프로그램에서 .eml로 저장해서 올려 주세요.")
    with olefile.OleFileIO(str(path)) as ole:
        return msg_from_ole(ole)


# ---------------------------------------------------------------- 공통
def read_mail(path: Path) -> MailInfo:
    if path.suffix.lower() == ".msg":
        return parse_msg(path)
    return parse_eml(path.read_bytes())


def mail_to_text(info: MailInfo) -> str:
    """LLM에 보낼 메일 텍스트: 머리글 + 본문 + 읽을 수 있는 첨부 문서의 글자."""
    from .loaders import SUPPORTED_SUFFIXES, extract_text

    sent = "알 수 없음"
    if info.sent:
        s = info.sent.astimezone(_KST) if info.sent.tzinfo else info.sent
        sent = s.strftime("%Y-%m-%d %H:%M") + f" ({'월화수목금토일'[s.weekday()]})"
    parts = [
        f"제목: {info.subject or '(제목 없음)'}",
        f"보낸 사람: {info.sender or '-'}",
        f"받는 사람: {info.to or '-'}",
        f"보낸 날짜: {sent}",
        "",
        info.body or "(본문 없음)",
    ]
    for att in info.attachments:
        suffix = Path(att.name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES or suffix in MAIL_SUFFIXES or not att.data:
            parts.append(f"\n[첨부: {att.name} — 읽지 않음]")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / f"attachment{suffix}"
            p.write_bytes(att.data)
            try:
                text = extract_text(p).strip()
            except Exception as e:  # 첨부 하나를 못 읽어도 메일 본문은 분석한다
                parts.append(f"\n[첨부: {att.name} — 읽지 못함: {e}]")
                continue
        if len(text) > _MAX_ATTACHMENT_CHARS:
            text = text[:_MAX_ATTACHMENT_CHARS] + "\n…(이하 생략)"
        parts.append(f"\n[첨부: {att.name}]\n{text}")
    return "\n".join(parts)


def mail_filename(info: MailInfo, raw: bytes, suffix: str = ".eml") -> str:
    """'20261002-0930_[인사] 연말정산 안내_1a2b3c.eml' 같은 겹치지 않는 파일 이름.

    같은 메일(Message-ID가 같거나 내용이 같은 메일)은 항상 같은 이름이 되어 두 번 저장되지 않는다.
    """
    stamp = info.sent.astimezone(_KST) if info.sent and info.sent.tzinfo else info.sent
    when = stamp.strftime("%Y%m%d-%H%M") if stamp else "nodate"
    subject = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", info.subject or "제목 없음")
    subject = " ".join(subject.split()).strip(" .")[:60] or "제목 없음"
    key = info.message_id.encode("utf-8") if info.message_id else raw
    digest = hashlib.sha1(key).hexdigest()[:6]
    return f"{when}_{subject}_{digest}{suffix}"


def save_mail(raw: bytes, mail_dir: Path, suffix: str = ".eml") -> tuple[Path, bool]:
    """메일 원본을 mail_dir에 저장. (경로, 새로 저장했는지)를 반환."""
    if suffix == ".msg":
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.msg"
            p.write_bytes(raw)
            info = parse_msg(p)
    else:
        info = parse_eml(raw)
    path = mail_dir / mail_filename(info, raw, suffix)
    if path.exists():
        return path, False
    mail_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.saving")
    tmp.write_bytes(raw)
    tmp.replace(path)
    return path, True


def compose_eml(subject: str, body: str, sent: dt.datetime | None = None, sender: str = "") -> bytes:
    """붙여넣은 메일 본문으로 .eml 원본을 만든다."""
    msg = EmailMessage()
    msg["Subject"] = subject.strip() or "붙여넣은 메일"
    if sender.strip():
        msg["From"] = sender.strip()
    sent = sent or dt.datetime.now(_KST)
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=_KST)
    msg["Date"] = format_datetime(sent)
    msg["X-People-Alarm"] = "pasted"
    msg.set_content(body)
    return msg.as_bytes(policy=email.policy.SMTP)


# ---------------------------------------------------------------- IMAP 가져오기
@dataclass(frozen=True)
class IMAPConfig:
    """사내 메일 서버(IMAP) 설정. 비밀번호는 환경 변수로만 받는다.

    PEOPLE_ALARM_IMAP_HOST      메일 서버 주소 (필수)
    PEOPLE_ALARM_IMAP_PORT      기본 993 (SSL), PEOPLE_ALARM_IMAP_SSL=0이면 143
    PEOPLE_ALARM_IMAP_USER      계정 (필수)
    PEOPLE_ALARM_IMAP_PASSWORD  비밀번호 (필수)
    PEOPLE_ALARM_IMAP_FOLDER    가져올 메일함 (기본 INBOX)
    PEOPLE_ALARM_IMAP_FROM      보낸 사람 필터 (예: noreply@intranet.company.co.kr)
    """

    host: str
    user: str
    password: str
    port: int = 993
    ssl: bool = True
    folder: str = "INBOX"
    sender: str = ""

    @classmethod
    def from_env(cls, **overrides) -> "IMAPConfig":
        env = os.environ
        use_ssl = env.get("PEOPLE_ALARM_IMAP_SSL", "1") != "0"
        values = dict(
            host=env.get("PEOPLE_ALARM_IMAP_HOST", ""),
            user=env.get("PEOPLE_ALARM_IMAP_USER", ""),
            password=env.get("PEOPLE_ALARM_IMAP_PASSWORD", ""),
            port=int(env.get("PEOPLE_ALARM_IMAP_PORT") or (993 if use_ssl else 143)),
            ssl=use_ssl,
            folder=env.get("PEOPLE_ALARM_IMAP_FOLDER", "INBOX"),
            sender=env.get("PEOPLE_ALARM_IMAP_FROM", ""),
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        missing = [n for n, k in (("PEOPLE_ALARM_IMAP_HOST", "host"), ("PEOPLE_ALARM_IMAP_USER", "user"),
                                  ("PEOPLE_ALARM_IMAP_PASSWORD", "password")) if not values[k]]
        if missing:
            raise ValueError(f"메일 서버 설정이 없습니다: {', '.join(missing)} 환경 변수를 설정하세요.")
        return cls(**values)


def _imap_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_imap(
    cfg: IMAPConfig,
    mail_dir: Path,
    since: dt.date,
    subject: str = "",
    connect=None,
) -> tuple[list[Path], int]:
    """since 이후 받은 메일 중 조건에 맞는 메일을 mail_dir에 .eml로 저장.

    메일을 읽음 처리하지 않는다(BODY.PEEK). 이미 저장한 메일은 건너뛴다.
    반환: (새로 저장한 파일 목록, 건너뛴 메일 수)
    """
    if connect is None:
        cls = imaplib.IMAP4_SSL if cfg.ssl else imaplib.IMAP4
        connect = lambda: cls(cfg.host, cfg.port)  # noqa: E731
    conn = connect()
    try:
        conn.login(cfg.user, cfg.password)
        status, _ = conn.select(_imap_quote(cfg.folder), readonly=True)
        if status != "OK":
            raise ValueError(f"메일함을 열지 못했습니다: {cfg.folder}")
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if cfg.sender:
            criteria += ["FROM", _imap_quote(cfg.sender)]
        charset = None
        if subject:
            # 한글 제목 검색은 UTF-8 리터럴로 보낸다.
            conn.literal = subject.encode("utf-8")
            criteria += ["SUBJECT"]
            charset = "UTF-8"
        status, data = conn.uid("SEARCH", *(["CHARSET", charset] if charset else []), *criteria)
        if status != "OK":
            raise ValueError("메일 검색에 실패했습니다.")
        uids = (data[0] or b"").split()
        saved: list[Path] = []
        skipped = 0
        for uid in uids:
            status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if status != "OK" or not raw:
                skipped += 1
                continue
            path, new = save_mail(raw, mail_dir)
            if new:
                saved.append(path)
            else:
                skipped += 1
        return saved, skipped
    finally:
        try:
            conn.logout()
        except Exception:
            pass

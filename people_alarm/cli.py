"""명령줄 인터페이스.

  people-alarm ingest            # docs/ 문서·메일에서 업무 추출 (바뀐 문서만)
  people-alarm add-mail <파일.eml|.msg|-> [--subject ...]  # 인트라넷 메일 추가 + 바로 분석
  people-alarm fetch-mail [--days 7]                         # 사내 메일 서버(IMAP)에서 가져와 분석
  people-alarm today|week|month|next-month [--date YYYY-MM-DD] [--save]
  people-alarm list              # 추출된 전체 업무
  people-alarm done <id> [--due YYYY-MM-DD]
  people-alarm serve [--port 8000]   # 웹 대시보드
  people-alarm export-html           # 대시보드를 HTML 파일 하나로 저장
  people-alarm check-llm             # 사내 LLM 서버 연결 확인
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

from .config import LLMConfig, active, make_client, set_active
from .loaders import find_documents
from .report import TITLES, build_report, render_markdown
from .schedule import occurrences
from .store import Store


def version_text() -> str:
    """실행 중인 코드의 버전과 위치 (예전 코드가 실행되는지 확인용)."""
    from . import __version__

    return f"people-alarm {__version__} (코드 위치: {Path(__file__).resolve().parent})"


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _analyze(paths: list[Path], store: Store, date: dt.date) -> int:
    """문서·메일을 차례로 분석해 저장. 실패한 개수를 반환."""
    from .analyzer import friendly_error
    from .extractor import extract_tasks

    failed = 0
    for path in paths:
        print(f"* 분석 중: {path.name} ...", flush=True)
        try:
            tasks = extract_tasks(path, reference_date=date)
        except Exception as e:  # 한 문서 실패가 전체를 멈추지 않도록
            failed += 1
            print(f"  ! 실패: {friendly_error(e)}", file=sys.stderr)
            continue
        store.replace_document_tasks(path, tasks)
        store.save()
        print(f"  → 업무 {len(tasks)}건 추출")
        for t in tasks:
            nxt = occurrences(t.schedule, date - dt.timedelta(days=14), date + dt.timedelta(days=400))
            print(f"     - [{t.category}] {t.title}  (마감: {nxt[0].isoformat() if nxt else '-'})")
    return failed


def cmd_ingest(args, store: Store) -> int:
    print(f"LLM: {active().model} @ {active().base_url}")

    docs = find_documents(args.docs)
    if not docs:
        print(f"{args.docs} 폴더에 문서가 없습니다. (.docx .pdf .xlsx .csv .txt .md .eml .msg)")
        return 1
    removed = store.remove_missing_documents({p.name for p in docs})
    for name in removed:
        print(f"- 삭제된 문서의 업무 제거: {name}")
    todo = []
    for path in docs:
        if not args.force and store.is_unchanged(path):
            print(f"= 변경 없음: {path.name}")
            continue
        todo.append(path)
    failed = _analyze(todo, store, args.date)
    store.save()
    print(f"\n총 {len(store.tasks)}건의 업무가 저장되었습니다: {store.tasks_path}")
    return 1 if failed else 0


def cmd_add_mail(args, store: Store) -> int:
    """메일 파일(.eml/.msg) 또는 붙여넣은 본문(-: 표준 입력, .txt)을 docs/mails/에 저장하고 분석."""
    from .loaders import check_format
    from .mail import MAIL_DIRNAME, compose_eml, save_mail

    mail_dir = args.docs / MAIL_DIRNAME
    sent = dt.datetime.combine(args.sent, dt.time(9)) if args.sent else None
    targets: list[Path] = []
    for src in args.files:
        try:
            if src == "-":
                raw, suffix = compose_eml(args.subject, sys.stdin.read(), sent, args.sender), ".eml"
            else:
                path = Path(src)
                suffix = path.suffix.lower()
                raw = path.read_bytes()
                if suffix in (".txt", ".md", ""):  # 본문만 복사해 둔 텍스트 파일
                    text = raw.decode("utf-8-sig", errors="replace")
                    raw, suffix = compose_eml(args.subject or path.stem, text, sent, args.sender), ".eml"
                elif suffix not in (".eml", ".msg"):
                    raise ValueError("메일 파일(.eml, .msg) 또는 본문 텍스트(.txt)만 넣을 수 있습니다.")
                check_format(f"mail{suffix}", raw)
            saved, new = save_mail(raw, mail_dir, suffix)
        except (OSError, ValueError) as e:
            print(f"! {src}: {e}", file=sys.stderr)
            return 1
        print(f"{'+ 저장' if new else '= 이미 있는 메일'}: {saved}")
        if new or args.force or not store.is_unchanged(saved):
            targets.append(saved)
    if not targets:
        return 0
    print(f"LLM: {active().model} @ {active().base_url}")
    return 1 if _analyze(targets, store, args.date) else 0


def cmd_fetch_mail(args, store: Store) -> int:
    """사내 메일 서버(IMAP)에서 인트라넷 메일을 가져와 docs/mails/에 저장하고 분석."""
    from .mail import MAIL_DIRNAME, IMAPConfig, fetch_imap

    try:
        cfg = IMAPConfig.from_env(folder=args.folder, sender=args.sender)
    except ValueError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    since = args.date - dt.timedelta(days=args.days)
    filters = [f"보낸 사람 {cfg.sender}"] if cfg.sender else []
    if args.subject:
        filters.append(f"제목 '{args.subject}'")
    print(f"메일 서버: {cfg.user}@{cfg.host} [{cfg.folder}]  {since.isoformat()} 이후" + (f", {', '.join(filters)}" if filters else ""))
    try:
        saved, skipped = fetch_imap(cfg, args.docs / MAIL_DIRNAME, since, subject=args.subject)
    except Exception as e:  # imaplib.IMAP4.error, OSError 등
        print(f"! 메일을 가져오지 못했습니다: {e}", file=sys.stderr)
        return 1
    print(f"새 메일 {len(saved)}통 저장, {skipped}통은 이미 있음")
    if not saved:
        return 0
    print(f"LLM: {active().model} @ {active().base_url}")
    return 1 if _analyze(saved, store, args.date) else 0


def cmd_report(args, store: Store) -> int:
    if not store.tasks:
        print("저장된 업무가 없습니다. 먼저 `people-alarm ingest`를 실행하세요.")
        return 1
    md = render_markdown(build_report(store, args.command, args.date))
    print(md)
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"{args.date.isoformat()}-{args.command}.md"
        path.write_text(md, encoding="utf-8")
        print(f"\n저장됨: {path}")
    return 0


def cmd_list(args, store: Store) -> int:
    for t in sorted(store.tasks, key=lambda t: (t.category, t.title)):
        s = t.schedule
        nxt = occurrences(s, args.date, args.date + dt.timedelta(days=400))
        nxt_s = nxt[0].isoformat() if nxt else "-"
        print(f"{t.id}  [{t.category}] {t.title}  ({s.frequency}, 다음: {nxt_s}, {t.lead_days}일 전 준비)  ← {t.source}")
    return 0


def cmd_done(args, store: Store) -> int:
    task = next((t for t in store.tasks if t.id == args.task_id), None)
    if task is None:
        print(f"업무 id를 찾을 수 없습니다: {args.task_id}")
        return 1
    due = args.due
    if due is None:  # 오늘 기준 가장 가까운 지난/오늘 마감, 없으면 다음 마감
        past = occurrences(task.schedule, args.date - dt.timedelta(days=60), args.date)
        future = occurrences(task.schedule, args.date, args.date + dt.timedelta(days=400))
        due = (past or future or [None])[-1 if past else 0]
        if due is None:
            print("완료 처리할 마감일을 찾지 못했습니다. --due로 지정하세요.")
            return 1
    store.mark_done(task.id, due.isoformat())
    store.save()
    print(f"완료 처리: {task.title} ({due.isoformat()})")
    return 0


def cmd_serve(args, store: Store) -> int:
    from .server import serve

    serve(args.data, args.host, args.port, default_date=args.fixed_date, docs_dir=args.docs)
    return 0


def cmd_export_html(args, store: Store) -> int:
    from .dashboard import build_payload, render_page

    payload = build_payload(store, args.date, mode="static", label=args.label)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_page(payload), encoding="utf-8")
    print(f"저장됨: {args.out}  (브라우저로 열면 됩니다)")
    return 0


def cmd_check_llm(args, store: Store) -> int:
    from .analyzer import friendly_error
    from .llm import LLMError

    cfg = active()
    print(version_text())
    print(f"서버: {cfg.base_url}\n모델: {cfg.model}\n인증: 사용 안 함")
    client = make_client(cfg)
    try:
        models = client.list_models()
    except LLMError as e:
        print(f"✗ 연결 실패: {friendly_error(e)}")
        return 1
    print(f"✓ 연결됨. 서버의 모델: {', '.join(models) or '(없음)'}")
    if cfg.model not in models:
        print(f"✗ '{cfg.model}' 모델이 목록에 없습니다. --llm-model 또는 PEOPLE_ALARM_LLM_MODEL로 위 이름 중 하나를 지정하세요.")
        return 1
    try:
        raw, _ = client.chat([{"role": "user", "content": "'OK'라고만 답하세요."}], max_tokens=1024)
    except LLMError as e:
        print(f"✗ 응답 테스트 실패: {friendly_error(e)}")
        return 1
    answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).split("</think>")[-1].strip()
    print(f"✓ 응답 테스트 성공: {answer[:80]!r}" if answer else "✓ 응답은 왔지만 본문이 비어 있습니다 (추론 단계에서 길이 제한에 걸렸을 수 있음).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="people-alarm", description="업무 문서 기반 시기별 업무 알림 agent")
    p.add_argument("--version", action="version", version=version_text())
    p.add_argument("--data", type=Path, default=Path("data"), help="업무 저장 폴더 (기본: data)")
    p.add_argument("--date", type=_date, default=dt.date.today(), help="기준일 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--llm-url", default=None, help="사내 LLM 서버 주소 (기본: PEOPLE_ALARM_LLM_URL 또는 http://75.12.15.121:8000/v1)")
    p.add_argument("--llm-model", default=None, help="모델 이름 (기본: PEOPLE_ALARM_LLM_MODEL 또는 thinkingcap)")
    sub = p.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="문서에서 업무 추출")
    ing.add_argument("--docs", type=Path, default=Path("docs"), help="문서 폴더 (기본: docs)")
    ing.add_argument("--force", action="store_true", help="변경 없는 문서도 다시 분석")

    am = sub.add_parser("add-mail", help="인트라넷 메일(.eml/.msg/본문)을 추가하고 바로 분석")
    am.add_argument("files", nargs="+", help="메일 파일 경로. '-'이면 표준 입력으로 받은 본문")
    am.add_argument("--docs", type=Path, default=Path("docs"), help="문서 폴더 (메일은 docs/mails에 저장)")
    am.add_argument("--subject", default="", help="본문만 넣을 때 메일 제목")
    am.add_argument("--sender", default="", help="본문만 넣을 때 보낸 사람/부서")
    am.add_argument("--sent", type=_date, default=None, help="본문만 넣을 때 메일 보낸 날짜 (기본: 오늘)")
    am.add_argument("--force", action="store_true", help="이미 분석한 메일도 다시 분석")

    fm = sub.add_parser("fetch-mail", help="사내 메일 서버(IMAP)에서 인트라넷 메일을 가져와 분석")
    fm.add_argument("--docs", type=Path, default=Path("docs"), help="문서 폴더 (메일은 docs/mails에 저장)")
    fm.add_argument("--days", type=int, default=7, help="며칠 전 메일부터 가져올지 (기본: 7)")
    fm.add_argument("--folder", default=None, help="메일함 (기본: PEOPLE_ALARM_IMAP_FOLDER 또는 INBOX)")
    fm.add_argument("--from", dest="sender", default=None, help="보낸 사람 필터 (기본: PEOPLE_ALARM_IMAP_FROM)")
    fm.add_argument("--subject", default="", help="제목에 이 말이 들어간 메일만")

    for name in TITLES:
        r = sub.add_parser(name, help=f"{TITLES[name]} 업무 리스트")
        r.add_argument("--save", action="store_true", help="마크다운 파일로 저장")
        r.add_argument("--out", type=Path, default=Path("reports"), help="저장 폴더 (기본: reports)")

    sub.add_parser("list", help="추출된 전체 업무 보기")

    d = sub.add_parser("done", help="업무 완료 처리")
    d.add_argument("task_id")
    d.add_argument("--due", type=_date, default=None, help="완료한 회차의 마감일")

    sv = sub.add_parser("serve", help="웹 대시보드 실행")
    sv.add_argument("--host", default="127.0.0.1", help="바인딩 주소 (기본: 127.0.0.1, 이 PC에서만 접속)")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--fixed-date", type=_date, default=None, help="기준일 고정 (기본: 접속한 날)")
    sv.add_argument("--docs", type=Path, default=Path("docs"), help="업로드 문서 저장 폴더 (기본: docs)")

    ex = sub.add_parser("export-html", help="대시보드를 HTML 파일 하나로 저장")
    ex.add_argument("--out", type=Path, default=Path("reports/dashboard.html"))
    ex.add_argument("--label", default="", help="대시보드 제목 옆에 붙일 설명")

    sub.add_parser("check-llm", help="사내 LLM 서버 연결과 모델 확인")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_active(LLMConfig.from_env(base_url=args.llm_url and args.llm_url.rstrip("/"), model=args.llm_model))
    store = Store(args.data)
    if args.command == "ingest":
        return cmd_ingest(args, store)
    if args.command == "add-mail":
        return cmd_add_mail(args, store)
    if args.command == "fetch-mail":
        return cmd_fetch_mail(args, store)
    if args.command == "list":
        return cmd_list(args, store)
    if args.command == "done":
        return cmd_done(args, store)
    if args.command == "serve":
        return cmd_serve(args, store)
    if args.command == "check-llm":
        return cmd_check_llm(args, store)
    if args.command == "export-html":
        return cmd_export_html(args, store)
    return cmd_report(args, store)


if __name__ == "__main__":
    sys.exit(main())

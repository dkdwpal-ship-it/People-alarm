"""명령줄 인터페이스.

  people-alarm ingest            # docs/ 문서에서 업무 추출 (바뀐 문서만)
  people-alarm today|week|month|next-month [--date YYYY-MM-DD] [--save]
  people-alarm list              # 추출된 전체 업무
  people-alarm done <id> [--due YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .loaders import find_documents
from .report import TITLES, build_report, render_markdown
from .schedule import occurrences
from .store import Store


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def cmd_ingest(args, store: Store) -> int:
    from .extractor import extract_tasks  # anthropic은 ingest에서만 필요

    docs = find_documents(args.docs)
    if not docs:
        print(f"{args.docs} 폴더에 문서가 없습니다. (.docx .pdf .xlsx .csv .txt .md)")
        return 1
    removed = store.remove_missing_documents({p.name for p in docs})
    for name in removed:
        print(f"- 삭제된 문서의 업무 제거: {name}")
    failed = 0
    for path in docs:
        if not args.force and store.is_unchanged(path):
            print(f"= 변경 없음: {path.name}")
            continue
        print(f"* 분석 중: {path.name} ...", flush=True)
        try:
            tasks = extract_tasks(path, reference_date=args.date)
        except Exception as e:  # 한 문서 실패가 전체를 멈추지 않도록
            failed += 1
            print(f"  ! 실패: {e}", file=sys.stderr)
            continue
        store.replace_document_tasks(path, tasks)
        store.save()
        print(f"  → 업무 {len(tasks)}건 추출")
    store.save()
    print(f"\n총 {len(store.tasks)}건의 업무가 저장되었습니다: {store.tasks_path}")
    return 1 if failed else 0


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="people-alarm", description="업무 문서 기반 시기별 업무 알림 agent")
    p.add_argument("--data", type=Path, default=Path("data"), help="업무 저장 폴더 (기본: data)")
    p.add_argument("--date", type=_date, default=dt.date.today(), help="기준일 YYYY-MM-DD (기본: 오늘)")
    sub = p.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="문서에서 업무 추출")
    ing.add_argument("--docs", type=Path, default=Path("docs"), help="문서 폴더 (기본: docs)")
    ing.add_argument("--force", action="store_true", help="변경 없는 문서도 다시 분석")

    for name in TITLES:
        r = sub.add_parser(name, help=f"{TITLES[name]} 업무 리스트")
        r.add_argument("--save", action="store_true", help="마크다운 파일로 저장")
        r.add_argument("--out", type=Path, default=Path("reports"), help="저장 폴더 (기본: reports)")

    sub.add_parser("list", help="추출된 전체 업무 보기")

    d = sub.add_parser("done", help="업무 완료 처리")
    d.add_argument("task_id")
    d.add_argument("--due", type=_date, default=None, help="완료한 회차의 마감일")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.data)
    if args.command == "ingest":
        return cmd_ingest(args, store)
    if args.command == "list":
        return cmd_list(args, store)
    if args.command == "done":
        return cmd_done(args, store)
    return cmd_report(args, store)


if __name__ == "__main__":
    sys.exit(main())

"""로컬 웹 대시보드 서버 (표준 라이브러리만 사용)."""

from __future__ import annotations

import datetime as dt
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import active
from .analyzer import MAX_UPLOAD_BYTES, Analyzer, Extractor
from .dashboard import build_payload, render_page
from .store import Store


def _default_extractor(path: Path, today: dt.date):
    from .extractor import extract_tasks

    return extract_tasks(path, reference_date=today)


def make_handler(
    data_dir: Path,
    default_date: dt.date | None,
    docs_dir: Path | None = None,
    extract: Extractor | None = None,
):
    today_fn = lambda: default_date or dt.date.today()  # noqa: E731
    analyzer = Analyzer(data_dir, docs_dir or Path("docs"), extract or _default_extractor, today_fn)

    class Handler(BaseHTTPRequestHandler):
        def _today(self, query: dict) -> dt.date:
            raw = query.get("date", [None])[0]
            return dt.date.fromisoformat(raw) if raw else today_fn()

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _read_body(self, limit: int) -> bytes | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "파일이 너무 큽니다 (최대 32MB).")
                self.close_connection = True
                return None
            return self.rfile.read(length)

        def _read_json(self) -> dict | None:
            # JSON 요청만 받아 다른 사이트의 단순 form 전송(CSRF)을 막는다.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "application/json만 허용됩니다.")
                return None
            raw = self._read_body(64 * 1024)
            if raw is None:
                return None
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._error(HTTPStatus.BAD_REQUEST, "JSON 형식이 올바르지 않습니다.")
                return None
            if not isinstance(body, dict):
                self._error(HTTPStatus.BAD_REQUEST, "JSON 객체가 필요합니다.")
                return None
            return body

        # ---- GET ----
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                today = self._today(query)
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "date는 YYYY-MM-DD 형식이어야 합니다.")
            if url.path == "/":
                html = render_page(build_payload(Store(data_dir), today, mode="server"))
                return self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/api/dashboard":
                return self._json(HTTPStatus.OK, build_payload(Store(data_dir), today, mode="server"))
            if url.path == "/api/documents":
                docs = analyzer.documents()
                busy = any(d["job"] and d["job"]["status"] in ("queued", "running") for d in docs)
                cfg = active()
                return self._json(HTTPStatus.OK, {"documents": docs, "busy": busy, "llm": {"model": cfg.model, "url": cfg.base_url}})
            self._error(HTTPStatus.NOT_FOUND, "not found")

        # ---- POST ----
        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/done":
                return self._post_done()
            if path == "/api/upload":
                return self._post_upload()
            if path == "/api/analyze":
                return self._post_analyze()
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def _post_done(self):
            body = self._read_json()
            if body is None:
                return
            try:
                task_id, due, done = str(body["taskId"]), str(body["due"]), bool(body["done"])
                dt.date.fromisoformat(due)
            except (KeyError, ValueError):
                return self._error(HTTPStatus.BAD_REQUEST, "taskId, due(YYYY-MM-DD), done 값이 필요합니다.")
            with analyzer.lock:
                store = Store(data_dir)
                if not any(t.id == task_id for t in store.tasks):
                    return self._error(HTTPStatus.NOT_FOUND, f"업무 id를 찾을 수 없습니다: {task_id}")
                (store.mark_done if done else store.unmark_done)(task_id, due)
                store.save()
                keys = store.done_keys
            self._json(HTTPStatus.OK, {"done": keys})

        def _post_upload(self):
            # 파일 본문을 그대로 받고, 이름은 X-Filename 헤더(URL 인코딩)로 받는다.
            # 사용자 정의 헤더가 필요하므로 다른 사이트에서 몰래 보낼 수 없다.
            raw_name = self.headers.get("X-Filename")
            if not raw_name:
                return self._error(HTTPStatus.BAD_REQUEST, "X-Filename 헤더가 필요합니다.")
            body = self._read_body(MAX_UPLOAD_BYTES)
            if body is None:
                return
            try:
                name = analyzer.save_upload(unquote(raw_name), body)
            except ValueError as e:
                return self._error(HTTPStatus.BAD_REQUEST, str(e))
            self._json(HTTPStatus.ACCEPTED, {"name": name})

        def _post_analyze(self):
            body = self._read_json()
            if body is None:
                return
            name = Path(str(body.get("name", ""))).name
            try:
                analyzer.submit(name)
            except FileNotFoundError:
                return self._error(HTTPStatus.NOT_FOUND, f"문서를 찾을 수 없습니다: {name}")
            self._json(HTTPStatus.ACCEPTED, {"name": name})

        def log_message(self, fmt, *args):  # 요청 로그는 조용히
            pass

    return Handler


def serve(data_dir: Path, host: str, port: int, default_date: dt.date | None = None, docs_dir: Path = Path("docs")) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(data_dir, default_date, docs_dir))
    print(f"대시보드: http://{host}:{port}  (문서 폴더: {docs_dir}, 종료: Ctrl+C)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

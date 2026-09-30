"""로컬 웹 대시보드 서버 (표준 라이브러리만 사용)."""

from __future__ import annotations

import datetime as dt
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .dashboard import build_payload, render_page
from .store import Store


def make_handler(data_dir: Path, default_date: dt.date | None):
    class Handler(BaseHTTPRequestHandler):
        def _today(self, query: dict) -> dt.date:
            raw = query.get("date", [None])[0]
            if raw:
                return dt.date.fromisoformat(raw)
            return default_date or dt.date.today()

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                today = self._today(query)
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "date는 YYYY-MM-DD 형식이어야 합니다."})
            if url.path == "/":
                html = render_page(build_payload(Store(data_dir), today, mode="server"))
                return self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/api/dashboard":
                return self._json(HTTPStatus.OK, build_payload(Store(data_dir), today, mode="server"))
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path != "/api/done":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            # JSON 요청만 받아 다른 사이트의 단순 form 전송(CSRF)을 막는다.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json만 허용됩니다."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                task_id, due, done = str(body["taskId"]), str(body["due"]), bool(body["done"])
                dt.date.fromisoformat(due)
            except (KeyError, ValueError, json.JSONDecodeError):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "taskId, due(YYYY-MM-DD), done 값이 필요합니다."})
            store = Store(data_dir)
            if not any(t.id == task_id for t in store.tasks):
                return self._json(HTTPStatus.NOT_FOUND, {"error": f"업무 id를 찾을 수 없습니다: {task_id}"})
            (store.mark_done if done else store.unmark_done)(task_id, due)
            store.save()
            self._json(HTTPStatus.OK, {"done": store.done_keys})

        def log_message(self, fmt, *args):  # 요청 로그는 조용히
            pass

    return Handler


def serve(data_dir: Path, host: str, port: int, default_date: dt.date | None = None) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(data_dir, default_date))
    print(f"대시보드: http://{host}:{port}  (종료: Ctrl+C)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

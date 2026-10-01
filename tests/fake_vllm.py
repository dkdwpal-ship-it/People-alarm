"""테스트용 가짜 vLLM 서버 (OpenAI 호환 /v1/models, /v1/chat/completions)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeVLLM:
    """behavior로 서버 반응을 바꾼다.

    reply(messages, request) -> (content, finish_reason) 를 지정하고,
    reject_schema=True면 response_format이 붙은 요청을 400으로 거절한다.
    """

    def __init__(self, reply, model="thinkingcap", reject_schema=False):
        self.reply = reply
        self.model = model
        self.reject_schema = reject_schema
        self.requests: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/v1/models":
                    return self._json(200, {"object": "list", "data": [{"id": fake.model, "object": "model"}]})
                self._json(404, {"error": "nf"})

            def do_POST(self):  # noqa: N802
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(req)
                if req["model"] != fake.model:
                    return self._json(404, {"object": "error", "message": f"The model `{req['model']}` does not exist.", "type": "NotFoundError", "code": 404})
                if fake.reject_schema and "response_format" in req:
                    return self._json(400, {"object": "error", "message": "json_schema not supported", "type": "BadRequestError", "code": 400})
                content, finish = fake.reply(req["messages"], req)
                self._json(200, {
                    "id": "c1", "object": "chat.completion", "created": 0, "model": fake.model,
                    "choices": [{"index": 0, "finish_reason": finish,
                                 "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def close(self):
        self.httpd.shutdown()

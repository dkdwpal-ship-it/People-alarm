"""사내 vLLM 서버(OpenAI 호환 API)용 최소 HTTP 클라이언트.

인증 헤더 없이 표준 라이브러리(urllib)만으로 호출한다.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from .config import LLMConfig


class LLMError(Exception):
    """LLM 호출 실패의 공통 부모."""


class LLMConnectionError(LLMError):
    """서버에 연결하지 못함."""


class LLMTimeoutError(LLMConnectionError):
    """응답 대기 시간 초과."""


class LLMHTTPError(LLMError):
    """서버가 오류 상태 코드로 응답함."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        # 사내 서버 IP로 직접 붙도록 기본은 HTTP(S)_PROXY 환경 변수를 무시한다.
        handlers = [] if cfg.use_proxy else [urllib.request.ProxyHandler({})]
        self._opener = urllib.request.build_opener(*handlers)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.cfg.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=self.cfg.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                err = parsed.get("error", parsed) if isinstance(parsed, dict) else parsed
                message = err.get("message", raw) if isinstance(err, dict) else str(err)
            except json.JSONDecodeError:
                message = raw or e.reason
            raise LLMHTTPError(e.code, str(message)) from e
        except (socket.timeout, TimeoutError) as e:
            raise LLMTimeoutError(f"{self.cfg.timeout}초 안에 응답이 없습니다.") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise LLMTimeoutError(f"{self.cfg.timeout}초 안에 응답이 없습니다.") from e
            raise LLMConnectionError(str(e.reason)) from e
        except (ConnectionError, OSError) as e:
            raise LLMConnectionError(str(e)) from e

    def list_models(self) -> list[str]:
        return [m["id"] for m in self._request("GET", "/models").get("data", [])]

    def chat(self, messages: list[dict], max_tokens: int, response_format: dict | None = None) -> tuple[str, str]:
        """(응답 본문, finish_reason)을 반환."""
        body = {"model": self.cfg.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if response_format:
            body["response_format"] = response_format
        data = self._request("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 서버 응답 형식이 예상과 다릅니다: {str(data)[:200]}") from e
        return (choice.get("message") or {}).get("content") or "", choice.get("finish_reason") or ""

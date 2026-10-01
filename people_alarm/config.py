"""사내 LLM(vLLM, OpenAI 호환 API) 연결 설정.

환경 변수로 바꿀 수 있다.
  PEOPLE_ALARM_LLM_URL          기본 http://75.12.15.121:8000/v1
  PEOPLE_ALARM_LLM_MODEL        기본 thinkingcap
  PEOPLE_ALARM_LLM_API_KEY      기본 없음 (vLLM에 --api-key를 걸었을 때만)
  PEOPLE_ALARM_LLM_TIMEOUT      요청 하나의 최대 대기 초 (기본 600)
  PEOPLE_ALARM_LLM_MAX_TOKENS   응답 최대 토큰 (기본 8192)
  PEOPLE_ALARM_LLM_CHUNK_CHARS  문서를 나눠 보낼 때 한 조각의 최대 글자 수 (기본 12000)
  PEOPLE_ALARM_LLM_USE_PROXY    1이면 HTTP(S)_PROXY 환경 변수를 따름 (기본 0: 사내 서버에 직접 연결)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

DEFAULT_URL = "http://75.12.15.121:8000/v1"
DEFAULT_MODEL = "thinkingcap"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout: int = 600
    max_tokens: int = 8192
    chunk_chars: int = 12000
    use_proxy: bool = False

    @classmethod
    def from_env(cls, **overrides) -> "LLMConfig":
        cfg = cls(
            base_url=os.environ.get("PEOPLE_ALARM_LLM_URL", DEFAULT_URL).rstrip("/"),
            model=os.environ.get("PEOPLE_ALARM_LLM_MODEL", DEFAULT_MODEL),
            api_key=os.environ.get("PEOPLE_ALARM_LLM_API_KEY", ""),
            timeout=_int("PEOPLE_ALARM_LLM_TIMEOUT", 600),
            max_tokens=_int("PEOPLE_ALARM_LLM_MAX_TOKENS", 8192),
            chunk_chars=_int("PEOPLE_ALARM_LLM_CHUNK_CHARS", 12000),
            use_proxy=os.environ.get("PEOPLE_ALARM_LLM_USE_PROXY", "0") == "1",
        )
        return replace(cfg, **{k: v for k, v in overrides.items() if v is not None})


_active: LLMConfig | None = None


def set_active(cfg: LLMConfig) -> None:
    """CLI 옵션으로 정한 설정을 프로세스 전체(대시보드 분석 포함)에 적용."""
    global _active
    _active = cfg


def active() -> LLMConfig:
    return _active or LLMConfig.from_env()


def make_client(cfg: LLMConfig):
    from openai import DefaultHttpxClient, OpenAI

    return OpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key or "EMPTY",  # vLLM은 키를 검사하지 않지만 SDK가 빈 값을 거부함
        timeout=cfg.timeout,
        max_retries=1,
        # 사내 서버 IP로 직접 붙도록 기본은 프록시 환경 변수를 무시한다.
        http_client=DefaultHttpxClient(trust_env=cfg.use_proxy, timeout=cfg.timeout),
    )

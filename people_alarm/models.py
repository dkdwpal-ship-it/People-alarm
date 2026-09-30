"""업무(Task)와 반복 일정(Schedule) 데이터 모델."""

from __future__ import annotations

import hashlib
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Schedule(BaseModel):
    """업무의 마감(기준)일 규칙.

    - once:    date(YYYY-MM-DD) 하루
    - weekly:  weekday(0=월 ~ 6=일)마다
    - monthly: 매월(또는 months에 있는 달만) day일, 또는 nth번째 weekday
    - yearly:  months에 있는 달의 day일 (분기 업무는 months=[3,6,9,12] 식으로 표현)
    """

    frequency: Literal["once", "weekly", "monthly", "yearly"]
    date: Optional[str] = Field(None, description="once일 때 YYYY-MM-DD")
    months: list[int] = Field(
        default_factory=list,
        description="해당 월 목록(1~12). yearly는 필수, monthly에서 비우면 매월",
    )
    day: Optional[int] = Field(None, description="일(1~31). -1은 말일")
    weekday: Optional[int] = Field(None, description="요일 0=월 ... 6=일")
    nth: Optional[int] = Field(
        None, description="monthly/yearly에서 n번째 weekday (1~5, -1=마지막 주)"
    )
    holiday_rule: Literal["none", "before", "after"] = Field(
        "none",
        description="기준일이 주말/공휴일이면: before=직전 영업일, after=다음 영업일, none=그대로",
    )


class ExtractedTask(BaseModel):
    """Claude가 문서에서 추출하는 업무 한 건."""

    title: str = Field(description="짧은 업무명")
    description: str = Field(description="해야 할 일과 주의사항 요약")
    category: str = Field(description="분류 (예: 급여, 4대보험, 세무, 평가, 채용, 교육)")
    owner: Optional[str] = Field(None, description="담당자/부서가 문서에 있으면")
    schedule: Schedule
    lead_days: int = Field(
        description="기준일 며칠 전부터 준비·알림이 필요한지 (문서에 없으면 합리적으로 추정)"
    )
    evidence: str = Field(description="근거가 된 문서 원문 문장(짧게)")


class ExtractionResult(BaseModel):
    tasks: list[ExtractedTask]


class Task(ExtractedTask):
    """저장소에 보관되는 업무 (출처 정보 포함)."""

    id: str
    source: str

    @classmethod
    def from_extracted(cls, t: ExtractedTask, source: str) -> "Task":
        key = f"{source}|{t.title}|{t.schedule.model_dump_json()}"
        tid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        return cls(id=tid, source=source, **t.model_dump())

"""업무(Task)와 반복 일정(Schedule) 데이터 모델."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, model_validator

Month = Annotated[int, Field(ge=1, le=12)]


class Schedule(BaseModel):
    """업무의 마감(기준)일 규칙.

    - once:    date(YYYY-MM-DD) 하루
    - weekly:  weekday(0=월 ~ 6=일)마다
    - monthly: 매월(또는 months에 있는 달만) day일, 또는 nth번째 weekday
    - yearly:  months에 있는 달의 day일 (분기 업무는 months=[3,6,9,12] 식으로 표현)
    """

    frequency: Literal["once", "weekly", "monthly", "yearly"]
    date: Optional[str] = Field(None, description="once일 때 YYYY-MM-DD")
    months: list[Month] = Field(
        default_factory=list,
        description="해당 월 목록(1~12). yearly는 필수, monthly에서 비우면 매월",
    )
    day: Optional[int] = Field(None, ge=-1, le=31, description="일(1~31). -1은 말일")
    weekday: Optional[int] = Field(None, ge=0, le=6, description="요일 0=월 ... 6=일")
    nth: Optional[int] = Field(
        None, ge=-1, le=5, description="monthly/yearly에서 n번째 weekday (1~5, -1=마지막 주)"
    )
    holiday_rule: Literal["none", "before", "after"] = Field(
        "none",
        description="기준일이 주말/공휴일이면: before=직전 영업일, after=다음 영업일, none=그대로",
    )

    @model_validator(mode="after")
    def _check_complete(self) -> "Schedule":
        """날짜를 계산할 수 없는 규칙은 거부한다 (LLM이 필드를 빠뜨린 경우)."""
        if self.day == 0:
            raise ValueError("day는 1~31 또는 -1이어야 합니다")
        if self.frequency == "once":
            if not self.date:
                raise ValueError("once에는 date가 필요합니다")
            dt.date.fromisoformat(self.date)
        elif self.frequency == "weekly":
            if self.weekday is None:
                raise ValueError("weekly에는 weekday가 필요합니다")
        else:
            by_nth = self.nth is not None and self.weekday is not None
            if self.day is None and not by_nth:
                raise ValueError("monthly/yearly에는 day 또는 nth+weekday가 필요합니다")
            if self.frequency == "yearly" and not self.months:
                raise ValueError("yearly에는 months가 필요합니다")
        return self


class ExtractedTask(BaseModel):
    """LLM이 문서에서 추출하는 업무 한 건."""

    title: str = Field(description="짧은 업무명")
    description: str = Field(description="해야 할 일과 주의사항 요약")
    category: str = Field(description="분류 (예: 급여, 4대보험, 세무, 평가, 채용, 교육)")
    owner: Optional[str] = Field(None, description="담당자/부서가 문서에 있으면")
    schedule: Schedule
    lead_days: int = Field(
        ge=0,
        le=366,
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

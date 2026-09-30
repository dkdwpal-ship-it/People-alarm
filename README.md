# People-alarm

업무 문서(Word·PDF·엑셀 등)를 읽어 **"언제 무엇을 해야 하는지"** 를 뽑아내고,
오늘 / 이번 주 / 이번 달 / 다음 달에 해야 할 업무 리스트를 자동으로 알려주는 agent입니다.

## 동작 방식

```
docs/ 업무 문서 ──(1) ingest: Claude가 문서 분석──▶ data/tasks.json (업무 + 반복 규칙)
                                                        │
                           (2) today/week/month: 날짜 계산(LLM 호출 없음)
                                                        ▼
                                  업무 리스트 (터미널 출력 + reports/*.md 저장)
```

1. **추출 (`ingest`)** — 문서마다 Claude(`claude-opus-5-5`)가 시기가 정해진 업무를 찾아
   `매월 25일`, `분기 말일`, `매주 월요일`, `매년 3월 10일` 같은 **반복 규칙**으로 구조화합니다.
   근거 문장(evidence)과 준비 기간(lead_days)도 함께 저장합니다.
   바뀐 문서만 다시 분석하므로(파일 해시 비교) 매번 비용이 들지 않습니다.
2. **조회 (`today`/`week`/`month`/`next-month`)** — 저장된 규칙으로 날짜를 계산합니다.
   한국 공휴일·주말을 반영해 "휴일이면 전/다음 영업일" 규칙을 적용하고, 다음을 보여줍니다.
   - 📅 해당 기간에 마감되는 업무 (D-day 표시)
   - 🔜 마감은 나중이지만 지금 준비를 시작해야 하는 업무
   - ⚠️ 최근 2주 안에 마감이 지났는데 완료 처리되지 않은 업무

## 설치

```bash
pip install -e .            # 개발용 테스트까지: pip install -e ".[dev]"
export ANTHROPIC_API_KEY=... # ingest(문서 분석)에만 필요
```

## 사용법

```bash
# 1) docs/ 폴더에 업무 문서를 넣고 분석
people-alarm ingest                 # 변경된 문서만 분석
people-alarm ingest --force         # 전부 다시 분석

# 2) 업무 리스트 보기
people-alarm today
people-alarm week
people-alarm month --save           # reports/2026-09-30-month.md 로 저장
people-alarm next-month --date 2026-12-01   # 기준일 지정

# 3) 관리
people-alarm list                   # 추출된 전체 업무와 다음 마감일
people-alarm done 5a016e1dac        # 업무 완료 처리 (id는 리스트에 표시됨)
```

샘플 데이터로 바로 체험하기 (API 키 불필요):

```bash
python -m people_alarm --data examples/data --date 2026-09-30 week
```

`examples/docs/`의 샘플 가이드에서 추출될 만한 결과를 `examples/data/tasks.json`에 넣어 두었습니다.

## 추출 결과 다듬기

`data/tasks.json`은 사람이 읽고 고칠 수 있는 JSON입니다. 추출이 틀렸다면 직접 수정해도 됩니다.
(단, 해당 문서가 바뀌어 다시 `ingest`되면 그 문서의 업무는 새 추출 결과로 교체됩니다.)

반복 규칙(`schedule`) 형식:

| 예시 | 규칙 |
|---|---|
| 매월 10일 | `{"frequency": "monthly", "day": 10}` |
| 매월 말일, 휴일이면 전 영업일 | `{"frequency": "monthly", "day": -1, "holiday_rule": "before"}` |
| 분기 말일 | `{"frequency": "yearly", "months": [3,6,9,12], "day": -1}` |
| 매년 3월 10일, 휴일이면 다음 영업일 | `{"frequency": "yearly", "months": [3], "day": 10, "holiday_rule": "after"}` |
| 매주 월요일 | `{"frequency": "weekly", "weekday": 0}` |
| 매월 둘째 주 화요일 | `{"frequency": "monthly", "nth": 2, "weekday": 1}` |
| 한 번만 | `{"frequency": "once", "date": "2026-11-03"}` |

## 자동으로 매일 받기

매일 아침 자동 실행하려면 cron에 등록하세요.

```cron
50 8 * * 1-5  cd /path/to/People-alarm && people-alarm today --save
```

## 프로젝트 구조

```
people_alarm/
  loaders.py    문서 → 텍스트/PDF 블록 변환 (docx, pdf, xlsx, csv, txt, md)
  extractor.py  Claude 구조화 출력으로 업무 추출
  models.py     Task / Schedule 데이터 모델
  schedule.py   반복 규칙 → 실제 날짜 계산 (한국 공휴일 반영)
  store.py      data/tasks.json, data/done.json 저장소
  report.py     기간별 리스트 계산 + 마크다운 렌더링
  cli.py        명령줄 인터페이스
tests/          pytest 테스트
```

## 참고

- HWP 파일은 직접 읽지 못합니다. 한글에서 PDF나 DOCX로 저장해 넣어 주세요.
- 문서 내용은 분석을 위해 Anthropic API로 전송됩니다. `docs/`, `data/`, `reports/`는 git에서 제외됩니다.

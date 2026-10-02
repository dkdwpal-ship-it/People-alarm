# People-alarm

업무 문서(Word·PDF·엑셀 등)를 읽어 **"언제 무엇을 해야 하는지"** 를 뽑아내고,
오늘 / 이번 주 / 이번 달 / 다음 달에 해야 할 업무 리스트를 자동으로 알려주는 agent입니다.

## 동작 방식

```
docs/ 업무 문서 ──(1) ingest: 사내 LLM이 문서 분석──▶ data/tasks.json (업무 + 반복 규칙)
                                                        │
                           (2) today/week/month: 날짜 계산(LLM 호출 없음)
                                                        ▼
                                  업무 리스트 (터미널 출력 + reports/*.md 저장)
```

1. **추출 (`ingest`)** — 문서에서 글자를 뽑아 사내 vLLM 서버(`thinkingcap`)에 보내고,
   시기가 정해진 업무를 `매월 25일`, `분기 말일`, `매주 월요일`, `매년 3월 10일` 같은
   **반복 규칙**으로 구조화합니다. 근거 문장(evidence)과 준비 기간(lead_days)도 함께 저장합니다.
   바뀐 문서만 다시 분석합니다(파일 해시 비교). 긴 문서는 나눠서 보내고 결과를 합칩니다.
2. **조회 (`today`/`week`/`month`/`next-month`)** — 저장된 규칙으로 날짜를 계산합니다.
   한국 공휴일·주말을 반영해 "휴일이면 전/다음 영업일" 규칙을 적용하고, 다음을 보여줍니다.
   - 📅 해당 기간에 마감되는 업무 (D-day 표시)
   - 🔜 마감은 나중이지만 지금 준비를 시작해야 하는 업무
   - ⚠️ 최근 2주 안에 마감이 지났는데 완료 처리되지 않은 업무

## 웹 대시보드

```bash
people-alarm serve                  # http://127.0.0.1:8000 에서 열기
people-alarm export-html            # reports/dashboard.html 파일 하나로 저장 (읽기 전용 스냅샷)
```

- **기간 타일**: 오늘 / 이번 주 / 이번 달 / 다음 달의 남은 업무 수. 누르면 아래 리스트가 바뀝니다.
- **업무 리스트**: 지난 마감(미완료) · 기간 내 마감 · 지금 준비할 업무. 체크박스로 완료 처리하면 `data/done.json`에 저장됩니다.
- **달력**: 한국 탁상달력처럼 일요일·공휴일은 빨강, 토요일은 파랑. 날짜를 누르면 그날 업무가 보입니다.
- **월별 마감 업무 수**: 앞으로 12개월 중 바쁜 달을 한눈에. 막대를 누르면 달력이 그 달로 이동합니다.
- **전체 업무**: 반복 규칙, 준비 기간, 다음 마감일, 출처 문서를 검색할 수 있는 표.
- 분류 칩으로 급여·세무 등 원하는 분류만 볼 수 있고, 기준일을 바꿔 과거·미래 시점도 볼 수 있습니다.

- **업무 문서** (`serve`에서만): 문서를 끌어다 놓거나 **문서 올리기** 버튼으로 올리면 `docs/`에 저장되고
  바로 분석이 시작됩니다. 한 건씩 차례로 분석하며, 끝나면 업무 리스트·달력이 새로고침 없이 갱신됩니다.
  문서마다 분석 상태(대기 중 / 분석 중 / 업무 n건 / 분석 실패)와 **다시 분석** 버튼이 있습니다.
  같은 이름으로 다시 올리면 그 문서의 업무가 새 분석 결과로 바뀝니다.

`serve`는 기본적으로 이 PC(127.0.0.1)에서만 접속됩니다. `export-html`로 만든 파일은 서버 없이 열리며,
완료 표시는 그 브라우저에만 저장됩니다.

샘플 데이터로 체험: `python -m people_alarm --data examples/data serve --fixed-date 2026-09-30`

## 설치

```bash
pip install -e .            # 개발용 테스트까지: pip install -e ".[dev]"
people-alarm check-llm      # 사내 LLM 서버 연결 확인
```

## 사내 LLM 설정

문서 분석은 사내 vLLM 서버(OpenAI 호환 API)를 씁니다. API 키나 인증 없이 서버 주소와 모델 이름만으로 연결합니다
(별도 라이브러리 없이 파이썬 기본 기능으로 호출하며, 인증 헤더를 보내지 않습니다).
기본값은 아래와 같고, 환경 변수나 명령 옵션으로 바꿀 수 있습니다.

| 항목 | 기본값 | 환경 변수 | 명령 옵션 |
|---|---|---|---|
| 서버 주소 | `http://75.12.15.121:8000/v1` | `PEOPLE_ALARM_LLM_URL` | `--llm-url` |
| 모델 | `thinkingcap` | `PEOPLE_ALARM_LLM_MODEL` | `--llm-model` |
| 요청 대기 시간(초) | 600 | `PEOPLE_ALARM_LLM_TIMEOUT` | |
| 응답 최대 토큰 | 8192 | `PEOPLE_ALARM_LLM_MAX_TOKENS` | |
| 문서 조각 크기(글자) | 12000 | `PEOPLE_ALARM_LLM_CHUNK_CHARS` | |
| 프록시 사용 | 안 함 | `PEOPLE_ALARM_LLM_USE_PROXY=1` | |

```bash
people-alarm check-llm                                   # 연결·모델 이름·응답 확인
people-alarm --llm-model other-model ingest              # 일회성으로 다른 모델 사용
```

- 사내 서버 IP로 직접 붙도록 기본적으로 `HTTP_PROXY` 같은 프록시 설정을 무시합니다.
  프록시를 거쳐야만 서버에 닿는 환경이면 `PEOPLE_ALARM_LLM_USE_PROXY=1`을 설정하세요.
- 서버가 JSON 스키마 강제(`response_format: json_schema`)를 지원하면 그것을 쓰고,
  지원하지 않으면 자동으로 일반 응답에서 JSON을 골라냅니다. `<think>…</think>` 추론 텍스트는 무시합니다.
- 모델 응답이 길이 제한에서 잘리면 문서 조각을 반으로 나눠 다시 시도합니다.
  "최대 입력 길이를 넘었다"는 오류가 나면 `PEOPLE_ALARM_LLM_CHUNK_CHARS`를 줄이고,
  "응답이 잘렸다"는 오류가 나면 `PEOPLE_ALARM_LLM_MAX_TOKENS`를 늘리세요.

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

샘플 데이터로 바로 체험하기 (LLM 서버 불필요):

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

## 문제 해결

**"ANTHROPIC_API_KEY를 설정하라"는 문구가 나와요** → 예전 버전 코드가 실행되고 있습니다.
지금 버전은 Anthropic을 쓰지 않고, 그 문구도 코드에 없습니다. 아래 순서로 다시 설치하세요.

```bash
# 1) 실행 중인 serve 창에서 Ctrl+C로 종료
git pull
pip uninstall -y people-alarm
pip install -e .            # -e를 꼭 붙이세요 (붙이지 않으면 코드가 복사되어 git pull이 반영되지 않음)
people-alarm --version      # people-alarm 0.3.0 (코드 위치: …/People-alarm/people_alarm) 이 나오면 정상
people-alarm serve
```

`serve`를 켜면 첫 줄에 버전과 코드 위치가, 대시보드의 업무 문서 칸 오른쪽 위에 버전이 표시됩니다.

## 프로젝트 구조

```
people_alarm/
  loaders.py    문서 → 텍스트 추출 (docx, pdf, xlsx, csv, txt, md) + 파일 형식 검사
  config.py     사내 LLM 연결 설정
  extractor.py  사내 LLM으로 업무 추출 (조각 나누기, JSON 해석·재시도)
  models.py     Task / Schedule 데이터 모델
  schedule.py   반복 규칙 → 실제 날짜 계산 (한국 공휴일 반영)
  store.py      data/tasks.json, data/done.json 저장소
  report.py     기간별 리스트 계산 + 마크다운 렌더링
  dashboard.py  대시보드 데이터(payload) + HTML 생성
  server.py     로컬 웹 서버 (serve)
  analyzer.py   업로드 문서 백그라운드 분석 큐
  web/dashboard.html  대시보드 화면
  cli.py        명령줄 인터페이스
tests/          pytest 테스트
```

## 참고

- HWP 파일은 직접 읽지 못합니다. 한글에서 PDF나 DOCX로 저장해 넣어 주세요.
- PDF는 글자 정보가 있어야 합니다. 스캔한 이미지 PDF는 원본 파일이나 OCR 처리한 PDF를 쓰세요.
- 문서 내용은 사내 LLM 서버로만 전송되고 외부로 나가지 않습니다. `docs/`, `data/`, `reports/`는 git에서 제외됩니다.

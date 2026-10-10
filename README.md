# 오프라인 특허 검토 후보 마킹 시스템

PPTX, PDF, DOCX, TXT, MD 보고자료를 읽어 특허 담당자가 검토해 볼 만한 기술 내용을 구간 단위로 표시한다. 외부 API 없이 CPU만으로 이 PC 안에서 동작하고, 사용자의 YES/NO/HOLD 판정을 모아 분류기를 다시 학습한다.

표시는 **특허 검토 후보**다. 등록 가능성, 신규성·진보성, 침해 여부는 판정하지 않는다. 숫자는 "후보 점수"이며 등록 확률이 아니다.

- 구현 기준: [PATENT_MARKING_SYSTEM_SPEC.md](PATENT_MARKING_SYSTEM_SPEC.md) v1.0
- 스펙에서 바꾼 점: [docs/SPEC_AMENDMENTS.md](docs/SPEC_AMENDMENTS.md)
- 구현 범위: Phase 0, 1A, 1B + PPTX/PDF 사본 마킹. Laya 2단계, active learning 큐, OCR, DOCX 사본 마킹은 미구현.
- Laya는 분석 흐름에 넣지 않았다. 1차 후보를 따로 다시 판단해 비교해 보는 실험 명령만 있다: [docs/LAYA_EXPERIMENT.md](docs/LAYA_EXPERIMENT.md)

## 동작 방식

```text
문서 → 기존 마킹 걷어내기(입력 파일은 그대로) → 파서(원문 위치 보존) → 논리 문단 → segment
     → 고정 multilingual-E5-small (ONNX Runtime, CPU) → 임베딩 캐시
     → [대상 임베딩 ; 문맥 임베딩] → Logistic Regression → 후보 점수
     → threshold 정책 → HTML 보고서 · JSONL · PPTX/PDF 마킹 사본 · 검토 화면
     → YES/NO/HOLD → 라벨 snapshot → 재학습 → 평가 → 승격(또는 롤백)
```

라벨이 없는 첫날에는 합성 예문으로 만든 seed 분류기(`classifier-0000`)가 임시 후보를 표시한다. 이 결과에는 항상 "SEED · 미검증"이 붙는다.

판정 단위는 설정 `segmentation.unit` 으로 고른다. 기본값 `paragraph` 는 불릿 묶음(상위 + 하위 불릿)·문단·표 행 단위이고, `fine` 은 불릿마다, 문장마다, 표는 셀마다 따로 판정하고 표시한다. 단위가 다르면 문서를 다시 나누고 분류기도 따로 만들어지므로, 두 단위를 함께 쓰려면 폴더를 따로 둔다([docs/OFFLINE_INSTALL.md](docs/OFFLINE_INSTALL.md) 3.6).

## 실행 방법

### 개발 PC에서 처음 준비 (인터넷 허용)

```bash
uv venv --python 3.11 .venv
```

```bash
uv pip install --python .venv/bin/python -r requirements/base.in -r requirements/dev.in
```

```bash
uv pip install --python .venv/bin/python --no-deps -r requirements/tokenizers.in -e .
```

```bash
.venv/bin/python tools/fetch_model.py --dest models/multilingual-e5-small
```

운영 PC(인터넷 없음)의 설치는 [docs/OFFLINE_INSTALL.md](docs/OFFLINE_INSTALL.md)를 따른다.

### Windows PC에서 (설치 묶음)

GitHub 릴리스의 설치 묶음(zip)에는 프로그램, 설치 패키지, 모델이 함께 들어 있다. Python 3.11~3.13(64비트)이 설치된 PC에서 쓴다.

1. zip을 풀어 나온 `patent_finder` 폴더를 원하는 곳(예: `D:\`)에 둔다.
2. `setup.bat` 을 더블클릭한다. 인터넷 없이 설치하고 점검까지 한다.
3. `mark.bat` 을 더블클릭하면 파일 열기 창이 뜬다. PPTX·PDF 파일을 고르면 분석하고 결과 폴더를 연다. 파일이나 폴더를 `mark.bat` 아이콘 위에 끌어다 놓아도 된다. 검토 화면은 `run.bat` 이다.
4. 판정이 쌓이면 `train.bat` 을 더블클릭한다. 판정으로 다시 학습하고 지금 모델과 비교해 보여 준 뒤, `y` 를 누르면 다음 분석부터 새 모델을 쓴다. 판정은 저절로 학습되지 않는다.

### 점검

```bash
.venv/bin/python -m patent_marker.cli doctor --offline
```

### 분석

```bash
.venv/bin/python -m patent_marker.cli analyze --input tests/fixtures/synthetic --output outputs/demo-001
```

출력 디렉터리 이름이 run 이름이 된다. 결과물:

| 파일 | 내용 |
|---|---|
| `report.html` | 표시된 구간 위치 목록과 문서별 본문. 파일 하나로 열리고 외부 자원을 쓰지 않는다 |
| `results.jsonl` | segment 한 줄. 점수, threshold, 모델·정책 버전, 원문 위치, 사람 판정 |
| `marked/*.marked.pptx` | 후보 문단 강조 + 도형 테두리 + 꼬리표 + 요약 슬라이드 |
| `marked/*.marked.pdf` | 후보 구간 highlight 주석 |
| `export_summary.json` | 결과물 목록과 처리하지 못한 파일 |

원본은 수정하지 않는다. 중단되면 같은 명령을 다시 실행해 이어서 진행한다.

입력 PPTX·PDF에 이미 마킹이 있으면 걷어낸 상태에서 시작한다. 대상은 이 도구가 만든 표시(꼬리표, 테두리, 요약 슬라이드), 글자 강조색(형광펜), 메모, PDF 표시 주석이다. 걷어낸 건수는 진행 출력과 보고서 경고, `export_summary.json` 에 남는다. 입력 파일에는 손대지 않으며, 마킹 사본에는 새 표시만 들어간다. 그래서 마킹 사본을 다시 넣어도 표시가 쌓이지 않는다.

### 검토

```bash
.venv/bin/python -m patent_marker.cli review
```

`http://127.0.0.1:8765/` 에서 연다. 127.0.0.1에만 바인딩한다.

- **페이지 검토**: 슬라이드/쪽에서 후보만 체크하고 "검토 완료"를 누르면 나머지는 NO로 기록된다. 키: `↑↓` 이동, `Space` 선택, `H` 보류, `Enter` 완료, `[` `]` 이전·다음.
- **검토 큐**: 후보만 / 전체 / 미검토 / 보류 / 비후보 표본 / 규칙 제안. 키: `J K` 이동, `Y N H` 판정.
- 판정 기준은 [docs/LABELING_GUIDE.md](docs/LABELING_GUIDE.md).

판정은 즉시 DB에 이벤트로 남는다. 운영 모델은 판정만으로 바뀌지 않는다.

### 학습 → 평가 → 승격

```bash
.venv/bin/python -m patent_marker.cli snapshot --name labels-v1
```

```bash
.venv/bin/python -m patent_marker.cli train --snapshot data/snapshots/labels-v1.jsonl
```

```bash
.venv/bin/python -m patent_marker.cli evaluate --model classifier-0001 --split test-v1
```

```bash
.venv/bin/python -m patent_marker.cli promote --model classifier-0001 --report artifacts/eval-0001.json
```

- `snapshot` 은 확정 YES/NO 라벨과 문서 계열 단위 train/validation/test 분할을 고정한다.
- `train` 은 train partition으로 학습하고 validation에서 Recall 목표(0.95)를 만족하는 가장 높은 threshold를 고른다.
- `promote` 는 기준(독립 test, 누수 없음, 검증된 threshold, end-to-end Recall ≥ 0.95, 양성 100개·문서 계열 20개 이상, 평가 문서 전체 검토)에 못 미치면 차단한다. 기준 미달 모델을 시범 사용하려면 `--stage pilot --reason "사유"` 를 쓴다. 결과물에 PILOT이 표시된다.
- `retrain` 은 위 네 단계를 한 번에 한다 (`train.bat` 이 쓰는 명령). snapshot → 학습 → test(없으면 validation) 문서로 새 모델과 지금 모델을 평가해 표로 보여 준 뒤, 운영 모델로 바꿀지 묻는다. 정식 승격 조건을 채우면 production, 아니면 pilot으로 올린다. `--promote yes|no` 로 묻지 않게 할 수 있고, 입력이 닫혀 있으면 바꾸지 않는다.

```bash
.venv/bin/python -m patent_marker.cli retrain
```

### 그 밖의 명령

| 명령 | 용도 |
|---|---|
| `rollback --model classifier-0000` | 이전 운영 모델과 그 정책으로 되돌리기 |
| `export --run demo-001 --format html,jsonl,annotated` | 저장된 run의 결과물 다시 만들기 (사람 판정 반영) |
| `experiment --snapshot … --modes separate,target_only,composed --c-values 0.3,1,3` | 특징 구성·C를 그룹 교차검증으로 비교 |
| `verify-run --run demo-001` | 과거 판정이 같은 bundle·캐시로 재현되는지 확인 |
| `mark 파일… 폴더…` | 여러 파일·폴더를 바로 분석. 결과는 `outputs/mark-날짜-시각`. `--pick` 을 주고 경로를 생략하면 파일 열기 창에서 고른다 (`mark.bat` 이 쓰는 명령) |
| `ingest --input …` | 분석 없이 문서만 넣기 (라벨링용) |
| `status` | 운영 모델, 라벨 수, 재학습 제안 여부 |
| `backup --output …` / `restore --from …` | DB 백업·복구 |
| `benchmark --segments 1000` | 임베딩 처리 성능 측정 |
| `laya-compare --run demo-001` | 실험: 저장된 run의 1차 후보를 Laya로 다시 판단해 비교 자료 만들기. 별도 환경(`.venv-laya`)에서만 동작하고 1차 결과는 바꾸지 않는다 ([docs/LAYA_EXPERIMENT.md](docs/LAYA_EXPERIMENT.md)) |

모든 명령에 `--config 경로` 를 줄 수 있다. 기본은 `./config/default.yaml`.

## 시험 자료

모두 합성 자료다. 실제 업무 문서는 저장소에 넣지 않는다.

| 위치 | 내용 |
|---|---|
| `tests/fixtures/synthetic/` | 형식별 작은 자료 5개 (PPTX, PDF, DOCX, TXT, MD) |
| `tests/fixtures/divisions/` | 사업부별 보고자료 6개: 광학솔루션·패키지솔루션·모빌리티솔루션 각각 PPTX와 PDF, 32~33장. 사업부 이름만 실제 조직명이고 과제·수치·방법은 지어낸 내용이다 |
| `tests/fixtures/division_expected_labels.csv` | 위 보고자료의 문단별 정답(YES/NO/HOLD, 작성 의도 기준) 537행 |

```bash
.venv/bin/python -m patent_marker.cli analyze --input tests/fixtures/divisions --output outputs/divisions-001
```

```bash
.venv/bin/python tools/check_expected.py --results outputs/divisions-001/results.jsonl
```

두 번째 명령은 분석 결과를 정답표와 대조해 문서·형식별 Recall, Precision, 놓친 후보, 잘못 표시한 구간을 보여준다. `--list` 를 붙이면 전부 나열한다. 자료를 다시 만들려면 `tests/fixtures/build_division_reports.py` 를 실행한다(reportlab 필요).

## 실제 지원 파일 범위

| 형식 | 추출하는 것 | 추출하지 못하는 것 |
|---|---|---|
| PPTX | 텍스트 상자, 자리표시자, 그룹 도형 안 텍스트, 표, SmartArt 텍스트, 차트 제목. 설정 시 발표자 노트 | 이미지 안 글자, 차트 데이터·범례, OLE 개체, 수식 |
| PDF (텍스트) | 문단, 목록, 선이 그려진 표, 다단 읽기 순서. 반복 머리말·쪽 번호 제거와 줄 끝 하이픈 병합은 기록 | 스캔 페이지, 글꼴 매핑이 깨진 페이지, 옆으로 누운 본문, 세로쓰기. 선 없는 표는 일반 문단으로 읽힌다 |
| DOCX | 본문 문단·목록·표(중첩 표 포함)·텍스트 상자, 추적 변경 삽입분. 설정 시 머리말·꼬리말·각주 | 이미지, 수식·OLE, 메모(comment) |
| TXT, MD | 문단, 목록, 마크다운 제목·표. UTF-8 외에는 `--encoding` 지정 | - |

PPT, DOC, HWP, HWPX, 매크로 문서, 암호화 문서, 이미지 파일은 미지원이다. 미지원·실패 문서와 일부만 처리된 문서는 "후보 없음"으로 처리하지 않고 보고서의 오류 목록과 경고에 남긴다.

사본 마킹은 PPTX와 PDF만 된다. PPTX 강조는 run 단위(표는 셀 단위)이고, 회전된 PDF 페이지는 위치를 확신할 수 없어 칠하지 않고 사유를 남긴다.

기존 마킹 중 걷어내지 않는 것: 일반 도형과 잉크(내용인지 표시인지 구분할 수 없다), PDF의 링크·양식 필드·첨부, 페이지 내용으로 그려진 색칠(예: 강조된 채로 PDF로 내보낸 글자 배경).

## 오프라인 반입 목록

| 종류 | 내용 |
|---|---|
| 저장소 | 코드, 설정, 합성 시험 자료, 문서 |
| 설치 패키지 | `requirements.lock` 의 패키지 28개 (sha256 포함). 설치 묶음에는 Windows x86-64의 Python 3.11·3.12·3.13용 wheel 46개가 들어 있다. torch·transformers·huggingface-hub·requests 없음 |
| 모델 | `intfloat/multilingual-e5-small` revision `614241f6…` 의 `model.onnx` (470MB), `tokenizer.json` (17MB), `config.json`, `MODEL_CARD.md`, `manifest.json`. MIT |

파일별 해시와 절차는 [docs/OFFLINE_INSTALL.md](docs/OFFLINE_INSTALL.md)에 있다. 세 가지를 한 파일로 묶은 Windows용 설치 묶음은 GitHub 릴리스에 있다.

## 검수 결과

2026-10-05, macOS 26 arm64 (10코어, RAM 24GiB), Python 3.11.15, 합성 자료 기준. `pytest` 177개 통과(단위 126, 통합 51. 그중 8개는 실제 E5 모델 사용). 실제 Laya 모델을 쓰는 시험 1개는 별도 환경(`.venv-laya`)에서 따로 통과했다. 2026-10-10에 GitHub의 Windows 러너(영문 Windows Server)에서 Python 3.11, 3.12, 3.13 각각으로 177개가 통과했다(실제 Laya 모델 시험 1개는 건너뜀).

### 스펙 17.1 기능·회귀 시험

| 항목 | 결과 | 근거 |
|---|---|---|
| 네트워크 가드를 켠 채 로컬 파일만으로 분석·재학습·export | 통과 | `test_cli_analyze_runs_fully_offline…`: 접속 시도 0건. CLI로 train·evaluate·promote까지 실행 |
| 반입 wheel만으로 설치 | 통과 (Windows 러너) | 설치 묶음과 같은 폴더에서 `setup.bat` 으로 인터넷 없이 설치하고 `mark.bat`, `run.bat`, `train.bat` 까지 실행. Python 3.11·3.12·3.13 각각. 릴리스 v0.1.6의 zip으로 설치·바꾸기·v0.1.4 위에 code-only zip 덮어쓰기까지 확인. 대상 회사 PC에서는 아직 설치해 보지 않음 |
| 모델 누락 시 즉시 오류, 다운로드 시도 없음 | 통과 | `test_missing_model_fails_without_any_download_attempt`, `test_cli_rejects_missing_model…` |
| GPU가 있어도 CPU 실행 | 통과 | CoreML provider가 있는 Mac에서 세션 provider가 CPU뿐임을 확인 |
| 혼용 텍스트·단위·부정문·목록·표·긴 문단·반복 문자열의 위치 보존 | 통과 | `test_text_processing.py`, `test_parsers.py` |
| 512토큰 초과 분할, 조용히 자르지 않음 | 통과 | `test_segment_over_model_limit_raises…`, `test_real_tokenizer_keeps_every_segment…`, `test_too_long_input_raises…` |
| PPTX 그룹/표, DOCX 문단-표 혼재, PDF 다단/회전/스캔 처리 또는 경고 | 통과 | `test_parsers.py` |
| 원본 해시 불변, 출력은 별도 파일 | 통과 | `test_originals_are_never_modified…` |
| YES/NO/HOLD와 수정 이력, 미검토·HOLD·파싱 오류의 학습 제외 | 통과 | `test_storage_feedback.py` |
| 같은 문서 계열이 train/test에 동시에 없음 | 통과 | `test_full_flow…`, `test_split_assignment_is_sticky…` |
| 과거 판정 재현 | 통과 | `verify-run`: 최대 점수 차이 5.6e-17, 판정 불일치 0건 |
| shadow on/off 출력 불변 | 실험 명령만 통과 | 분석 흐름은 `laya.mode: off` 만 허용한다. 실험 명령 `laya-compare` 가 1차 판정, 기존 결과물, 다른 DB 표를 바꾸지 않음을 시험 (`test_comparison_adds_results_and_leaves_first_stage_outputs_untouched`) |
| 승격·롤백 시 bundle 호환성 검사, 진행 중 run의 버전 불변 | 통과 | `test_bundle_roundtrip…`, `test_interrupted_run_resumes…` |
| 중단·재시작 시 DB 중복과 손상 캐시 없음 | 통과 | `test_interrupted_run_resumes…`, `test_embedding_cache_reuses…` |
| 기존 마킹을 걷어내고 시작 (스펙 외 추가) | 통과 | `test_existing_marks.py`: 마킹 사본을 다시 넣어도 본문·후보가 원본과 같고 표시가 쌓이지 않음. 작성자 형광펜·메모·주석은 사본에서만 제거되고 입력 파일 해시는 불변 |

### 스펙 17.2 품질·성능 검수

| 항목 | 결과 |
|---|---|
| Recall·Precision·F2·검토 비율·FN 목록·신뢰구간 보고 | 보고서 기능은 구현·시험됨. **사내 데이터로 측정한 값은 없다** |
| end-to-end Recall 0.95 | **미측정.** 실제 문서와 라벨이 필요하다 |
| peak RSS 8GiB 이하 | 이 Mac에서 1.6~1.7GiB. 대상 16GB PC에서는 미측정 |
| 1,000 segment 10분 이내 | 이 Mac에서 3.2초 (4 threads, batch 8, 토큰 길이 중앙값 41·최대 100, 합성 문장). cold start 0.5초, 단건 p50 11ms·p95 13ms. 대상 PC에서는 미측정 |
| 업무 최대 크기 문서 | 미시험 |
| 사본 주석 위치 | PDF: highlight 영역의 글자가 대상 구간과 일치함을 자동 시험하고 렌더링으로도 확인. PPTX: 표시 위치와 XML 순서를 자동 시험. **PowerPoint에서 직접 여는 확인은 못 했다** |

합성 seed 분류기는 작은 합성 자료 64개 구간 중 16개를 후보로 표시했고, 시험에서 기대값으로 지정한 기술 문장 5개를 모두 포함했다.

사업부별 합성 보고자료 6개(구간 1,006개)를 정답표와 대조한 결과는 아래와 같다. 정답은 자료를 만든 쪽의 작성 의도이고 seed 분류기는 미검증 상태다.

| 형식 | Recall | Precision | 표시 비율 | 놓친 후보 | 잘못 표시 | 추출 누락 |
|---|---|---|---|---|---|---|
| PPTX | 0.957 (110/115) | 0.797 | 0.261 | 5 | 28 | 0 |
| PDF | 0.955 (107/112) | 0.709 | 0.291 | 5 | 44 | 0 |

잘못 표시한 72건은 수단 없이 문제나 결과만 적은 불릿 문장 32건(그중 "배경" 문장 16건), 블록도의 짧은 블록 이름 18건, 슬라이드 제목 16건이 대부분이다. 블록 이름 18건 중 17건은 PDF에서 나왔다. PDF에서는 블록 이름들이 한 줄로 합쳐져 한 구간으로 추출된다. 놓친 후보는 두 형식 모두 표 안의 짧은 조건 문장이었다.

2026-10-05에 대조 도구(`tools/check_expected.py`)의 짝짓기 규칙을 고쳤다. 그 전에는 블록 이름 같은 짧은 항목을 그 낱말이 들어간 다른 후보 문장에도 짝지어, 잘못 표시를 PPTX 37건, PDF 48건으로 실제보다 많게 셌다. Recall은 그대로다.

같은 자료에서 1차 후보 222개를 Laya로 다시 판단해 '이견'인 후보를 빼 봤다(실험, 동의 기준 0.5). 헛표시는 줄었지만 진짜 후보도 함께 잃어, 걸러내는 용도로는 쓸 수 없는 결과다. 다른 기준에서의 수치와 문장 종류별 결과는 [docs/LAYA_EXPERIMENT.md](docs/LAYA_EXPERIMENT.md)에 있다.

| 형식 | Recall | Precision | 표시 비율 | 진짜 후보 | 헛표시 |
|---|---|---|---|---|---|
| PPTX | 0.957 → 0.870 | 0.797 → 0.909 | 0.261 → 0.208 | 110 → 100 | 28 → 10 |
| PDF | 0.955 → 0.875 | 0.709 → 0.797 | 0.291 → 0.237 | 107 → 98 | 44 → 25 |

판정 단위를 잘게 한 `segmentation.unit: fine` 으로 같은 자료를 분석한 결과다. seed 분류기는 문장·조각 단위에서 점수가 낮아져 놓침이 늘었다. 문서 2개의 판정으로 다시 학습했다고 보고 나머지 1개에서 잰 모의 실험(PPTX)에서는 놓침이 회복되지만 헛표시가 늘었다 (paragraph 1 놓침·8 헛표시, fine 3 놓침·49 헛표시).

| 단위 | 형식 | Recall | Precision | 표시 비율 | 놓친 후보 | 잘못 표시 | 구간 수 |
|---|---|---|---|---|---|---|---|
| paragraph (기본) | PPTX | 0.957 | 0.797 | 0.261 | 5 | 28 | 1,006 (PDF 포함) |
| fine | PPTX | 0.861 | 0.786 | 0.239 | 16 | 27 | 1,504 (PDF 포함) |
| paragraph (기본) | PDF | 0.955 | 0.709 | 0.291 | 5 | 44 | |
| fine | PDF | 0.857 | 0.691 | 0.268 | 16 | 43 | |

이 수치는 모두 합성 자료에 대한 것이며 실제 문서에서의 성능을 뜻하지 않는다.

## 남은 제한

- seed 분류기와 그 threshold는 실제 문서에서 검증되지 않았다. 놓치는 후보가 있을 수 있다.
- 판정 단위 `fine` 은 합성 자료에서 seed 분류기의 놓침이 늘었고(위 표), 판정으로 다시 학습한 뒤의 효과는 모의 실험으로만 봤다. 표시가 잘게 붙는 대신 조각 하나만 보고 판정해야 한다.
- 대상 회사 PC(한국어 Windows 10/11)에서의 설치·실행은 확인하지 않았다. Windows에서는 GitHub의 러너(영문 Windows Server)에서만 확인했다. 16GB 메모리 조건과 실제 크기 문서도 확인하지 않았다.
- 분석 흐름 안의 Laya 2단계(shadow/assist/filter), active learning 큐 배분, OCR, DOCX 사본 마킹은 구현하지 않았다. Laya는 따로 돌려 비교하는 실험 명령만 있고, 기본 설치와 설치 묶음에는 들어 있지 않다.
- 작성자가 직접 넣은 형광펜·메모·PDF 주석은 마킹 사본에 남지 않는다(입력 파일에는 그대로 있다). 일반 도형, 잉크, 페이지 내용으로 그려진 색칠은 지우지 못한다.
- 마킹 사본을 다시 넣으면 원본과 별개의 문서로 저장된다(같은 문서 계열). 원본에 준 판정은 이어지지 않는다.
- 글꼴이 포함되지 않은 한글 CID 글꼴 PDF에서는 가운뎃점 같은 일부 기호가 추출되지 않는 것을 확인했다(추출 라이브러리의 문자 대응표 한계).
- PDF 줄바꿈은 공백으로 잇는다. 한글 단어 중간에서 줄이 바뀐 문서는 단어 사이에 공백이 생긴다.
- PDF 읽기 순서는 여백 기준 추정이다. 다단이 감지된 쪽은 경고를 낸다.
- 문서 계열(개정본·템플릿) 판정은 문단 겹침 기준의 추정이다.
- 검토자 간 충돌은 HOLD로 처리되지만 합의 전용 화면은 없다. 한쪽이 판정을 고쳐야 풀린다.
- 검토자 이름 기본값은 OS 사용자명이다. 실행 환경에 따라 달라질 수 있으니 `review.reviewer_id` 를 지정한다.
- 프로그램 안의 네트워크 가드는 Python 소켓 수준이다. 최종 보장은 OS·방화벽 차단이다.
- 메모리 예산을 넘으면 배치를 줄이고 그래도 넘으면 중단한다. 이 경로는 실제 초과 상황에서 시험하지 않았다.

## 저장소 구조

```text
setup.bat, mark.bat, run.bat, train.bat   Windows용 설치, 분석, 검토 화면, 판정으로 다시 학습
config/default.yaml            기본 설정
src/patent_marker/
  cli.py                       명령줄 진입점
  retrain.py                   판정으로 다시 학습 → 평가 → 확인 뒤 교체 (train.bat)
  dropped.py                   끌어다 놓은 파일 이름 되살리기 (mark.bat)
  pickdialog.py                파일 열기 창 (mark.bat을 더블클릭했을 때)
  parsers/                     text, docx, pptx, pdf
  segmentation/                정규화, offset, 문장 분할, 논리 문단, segment, 문맥
  embeddings/                  E5(ONNX), 캐시, 특징 구성, 모델 manifest 검증
  classifiers/                 학습, 추론, bundle, registry, seed
  policies/                    threshold, 문단 집계
  storage/                     SQLite, migration
  feedback/                    이벤트, 확정 라벨, 검토 큐, snapshot
  evaluation/                  분할, 지표, 보고서
  export/                      html, jsonl, 사본 마킹
  second_stage/                2단계 어댑터 인터페이스 (off), Laya 비교 실험 (laya.py, compare.py)
  ui/                          로컬 검토 화면
  seed_data/                   합성 seed 예문
tests/                         단위·통합 시험, 합성 시험 자료
tools/                         반입 준비 도구 (준비 환경 전용), 설치 본체 windows_setup.py
docs/                          라벨 가이드, 오프라인 설치, 스펙 변경 내역, Laya 비교 실험
.github/workflows/windows.yml  Windows 러너에서 설치와 시험 확인
```

`models/`, `data/`, `artifacts/`, `outputs/`, `vendor/wheels/`, `.venv-laya/` 는 Git에 넣지 않는다. 실제 보고자료, 라벨, DB, 임베딩, 모델 가중치도 넣지 않는다.

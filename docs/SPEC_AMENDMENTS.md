# 스펙 대비 변경 내역

기준 문서는 저장소 루트의 `PATENT_MARKING_SYSTEM_SPEC.md` (v1.0, 2026-10-04)이며 원문은 수정하지 않았다.
아래는 2026-10-05에 검토 후 채택한 변경과, 구현하면서 메운 빈틈이다. 여기에 없는 내용은 스펙 그대로다.

## 채택한 변경

| # | 스펙 조항 | 변경 | 이유 | 스펙 원안으로 되돌리는 법 |
|---|---|---|---|---|
| A1 | 5.2, 6 | 대상과 문맥을 따로 임베딩해 `[대상 ; 문맥]` 특징으로 결합 (`features.mode: separate`) | 동결 인코더 + 평균 풀링에서는 `[판정 대상]` 표식이 구분되지 않고, 짧은 불릿의 벡터가 문맥에 지배된다 | `features.mode: composed` 로 바꾸고 다시 학습. `experiment` 명령으로 두 방식을 교차검증 비교 가능 |
| A2 | 6, 15 | 추론 런타임을 PyTorch + transformers 대신 ONNX Runtime + tokenizers 로 | 반입 패키지가 작고, 다운로드 기능 라이브러리(huggingface-hub 등)를 운영 환경에 두지 않으며, CPU provider만 명시한다 | 인코더 구현(`embeddings/e5.py`) 교체가 필요하다. 설정만으로는 되돌릴 수 없다 |
| A3 | 7.2 | 합성 seed 예문으로 만든 `classifier-0000` 을 라벨이 없을 때 임시로 사용 (SEED · 미검증) | 라벨이 쌓이기 전에는 마킹이 전혀 나오지 않는 문제 | `seed.enabled: false` → 운영 모델이 없으면 UNTRAINED |
| A4 | 13, 16 | PPTX·PDF 사본 마킹을 1차 범위에 포함 | 보고자가 자기 자료에서 표시를 보는 것이 원래 목적 | `export.formats` 에서 `annotated` 제거 |
| A5 | 9, 11, 13 | 슬라이드/페이지 단위 일괄 검토: 선택한 구간은 YES, 나머지는 '묵시적 NO' | 건별 판정보다 클릭이 적고, 페이지 전체가 라벨링되어 선택 편향이 줄어든다 | 검토 큐 탭에서 건별로만 판정 |

| A6 | 4, 13 | 입력 PPTX·PDF에 이미 있는 마킹을 모두 걷어낸 상태에서 분석과 사본 마킹을 시작 | 마킹 사본을 다시 넣으면 꼬리표·요약 슬라이드가 본문으로 읽히고 표시가 겹쳐 쌓인다 | `existing_marks.py` 의 걷어내기 범위를 바꿔야 한다. 설정 항목은 없다 |

채택하지 않은 제안: 2단 우선순위 표시, 과거 판정·유사 사례 표시, 도구 표시만 걷어내고 작성자 표시는 보존하는 방식.

### A1의 세부 규칙

- 문맥 임베딩은 같은 섹션(슬라이드, 쪽, 제목 범위) 안의 제목·이전·다음 segment 임베딩의 평균을 L2 정규화한 것이다. 문맥이 없으면 0 벡터.
- 문맥 특징은 이웃 segment의 임베딩을 재사용하므로 추가 모델 호출이 없다.
- 어느 방식이 나은지는 사내 라벨로 확인해야 한다. 합성 말뭉치(문서 안에서 YES/NO 문단을 섞어 둔 TXT 24개, 라벨 288개)에 대한 그룹 교차검증에서는 `separate` 와 `target_only` 가 AP 1.00 · 검토 비율 0.40, `composed` 가 AP 0.78 · 검토 비율 0.76~0.77 이었다. 이 말뭉치는 이웃 문단이 무작위로 섞여 있어 차이가 과장될 수 있다.

### A3의 세부 규칙

- seed 예문은 `src/patent_marker/seed_data/seed_v1.jsonl` (YES 130, NO 157, 모두 합성)이다.
- seed 모델의 threshold는 seed 예문의 교차검증으로만 정하며 정책 상태는 `SEED_UNVALIDATED` 다. 실제 문서에서의 Recall은 측정되지 않았고, 모든 화면과 결과물에 "임시 후보 (SEED · 미검증)"로 표시한다.
- 사람 라벨로 학습할 때 seed 예문은 `seed.sample_weight` (기본 0.3)로 함께 쓴다. 평가와 threshold 선택은 사람 라벨로만 한다.
- 회사 PC에서 과거 발명신고서·행정 문서를 약한 라벨로 넣는 기능은 구현하지 않았다.

### A5의 세부 규칙

- 묵시적 NO는 이벤트 `source = page_review_implicit` 로 구분해 저장하고, 확정 라벨의 `implicit` 플래그와 평가 보고서의 `label_source` 구분으로 따로 볼 수 있다.
- 묵시적 NO도 학습에 쓴다(가중치 1). 화면은 후보를 미리 체크해 두지 않으며, 체크하지 않은 시스템 후보가 몇 건인지 완료 전에 보여준다.

### A6의 세부 규칙 (2026-10-05 추가)

- 걷어내는 것
  - PPTX: 이 도구가 만든 도형(`PM_MARK_*`, `PM_SUMMARY_*`)과 요약 슬라이드, 글자 강조색(`a:highlight`, 색과 무관), 메모(기존 형식과 최신 스레드 형식).
  - PDF: 표시 주석(Highlight, Underline, StrikeOut, Squiggly, Text, FreeText, Ink, Line, Square, Circle, Polygon, PolyLine, Stamp, Caret, Redact와 딸린 Popup). 걷어낸 주석 개체는 출력 파일에 남기지 않는다.
- 걷어내지 않는 것: 일반 도형과 잉크(내용인지 표시인지 구분할 수 없다), PDF의 Link·Widget·FileAttachment, 페이지 내용으로 그려진 색칠.
- 입력 파일은 고치지 않는다. PPTX 파서와 사본 마킹이 같은 함수(`clear_pptx_marks`)를 메모리 안에서 적용하므로 두 단계가 보는 슬라이드 번호와 도형 구조가 같다. 요약 슬라이드는 슬라이드 수에 넣지 않는다.
- 걷어낸 건수는 문서의 `coverage.existing_marks`, 보고서 경고(`existing_marks_cleared`), `export_summary.json` 의 `cleared` 에 기록한다.
- 작성자가 직접 넣은 형광펜·메모·주석도 마킹 사본에는 남지 않는다. 이 점을 알고 선택한 동작이다.
- 마킹 사본을 다시 넣으면 파일 해시가 달라 별개의 문서로 저장된다(문단이 겹치므로 같은 문서 계열). 원본의 판정 이력은 이어지지 않는다.
- 마킹 사본의 이름은 `이름.marked.확장자` 이며, 입력이 이미 `.marked` 로 끝나면 덧붙이지 않는다. 한 번의 내보내기에서 이름이 겹치면 `-2`, `-3` 을 붙인다.
- 파서 동작이 바뀌어 `PARSER_VERSION` 을 `parser-2` 로 올렸다(이후 PDF 수정으로 `parser-3`). 이전 버전으로 넣은 문서는 다시 수집된다.

## 구현하면서 메운 빈틈

| 스펙 조항 | 내용 |
|---|---|
| 14.3 | 명령 추가: `snapshot` (라벨 snapshot·분할 고정), `experiment` (특징 구성·C 비교), `backup`/`restore`, `verify-run` (과거 판정 재현), `benchmark`, `status` |
| 12.4, 14.3 | 승격 단계 구분: `promote --stage production` 은 기준 미달이면 차단. `--stage pilot --reason "…"` 은 누수가 없고 threshold가 있는 모델을 PILOT으로 올리며 결과물에 PILOT을 표시 |
| 7.3 | 문단 정답 규칙: 하위 segment 중 YES가 있으면 YES, 모든 segment가 NO이면 NO, 그 외는 미정(평가 제외). threshold는 문단 단위 max 점수로 고른다 |
| 7.3 | validation의 양성 문단이 `policy.min_validation_positives` (기본 10) 미만이면 train+validation의 그룹 교차검증 out-of-fold 점수로 threshold를 추정하고 상태를 `CV_ESTIMATE` 로 둔다. 이 상태로는 운영 승격이 안 된다 |
| 12.1 | 문서 계열(`document_family_id`)은 같은 내용이거나 문단의 절반 이상(3개 이상)이 겹치는 기존 문서가 있으면 그 계열로 배정한다. 계열의 partition 배정은 한 번 정하면 바뀌지 않는다 |
| 12.3 | 파서가 읽지 못한 후보는 검토 화면의 "파싱 누락 후보 등록"으로 남기고 end-to-end Recall에서 FN으로 센다 |
| 4.1 | PPTX의 SmartArt 텍스트와 차트 제목을 추출한다(차트 데이터·범례는 제외) |
| 4.2 | 본문이 통째로 옆으로 누운 PDF 페이지는 분석하지 못하며 '처리됨'으로 세지 않는다. 모든 페이지가 그런 문서는 unsupported |
| 4.2 | PDF 글머리 기호: 전용 기호(•, ■, ※ 등)는 뒤에 공백이 없어도 목록 표식으로 본다. `-`, `*`, `+` 는 공백이 있을 때만 표식이다 (`parser-3`) |
| 4.2 | PDF 위·아래 8% 영역의 줄은 단 나눔 분석에서 떼어 따로 다룬다. 좌우 2단 쪽에서도 반복 꼬리말을 찾아 지울 수 있다 (`parser-3`) |
| 4.2, 5.2 | PDF 목록의 묶음 단위와 들여쓰기 수준은 같은 단(column) 전체를 기준으로 정한다. 항목 사이 간격이 넓어도 상위·하위 불릿을 묶을 수 있다 (`parser-3`) |
| 2 | `runtime.offline: true` 이면 프로세스 안에 네트워크 가드를 설치해 루프백 외 접속·DNS 조회를 차단한다 |
| 14.2 | `policy.candidate_threshold` 에 숫자를 넣으면 설정 오류다. threshold는 정책 버전으로만 관리한다 |

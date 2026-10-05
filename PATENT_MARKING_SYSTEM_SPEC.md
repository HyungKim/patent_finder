# 오프라인 특허 검토 후보 마킹 시스템 구현 명세서

- 문서 버전: 1.0
- 작성일: 2026-10-04
- 대상: 회사 PC의 VSCode/Git 환경 및 AI 코딩 도구
- 구현 언어: Python
- 문서 형식: UTF-8 Markdown, Git 저장소 루트에 배치 가능
- 상태: 구현 기준안. 수치 목표는 사내 데이터로 검증할 제안값이며 성능 측정 결과가 아니다.

## 1. 목적과 판단 범위

한국어 중심의 기술 보고자료에서 특허 담당자가 검토할 가치가 있는 기술적 아이디어를 문단 단위로 찾고 표시한다. 사용자의 판단을 축적하여 회사의 기술 분야와 검토 기준에 맞는 분류기를 개선한다.

시스템의 출력은 **특허 검토 후보**이다. 등록 가능성, 신규성·진보성, 침해 여부를 판정하지 않는다. 선행기술 검색이나 출원 서류 자동 작성은 이 프로젝트의 범위에 포함하지 않는다.

우선순위는 다음과 같다.

1. 검토할 후보를 놓치지 않는 것: Recall 우선.
2. 원문 위치와 주변 문맥을 정확하게 보여주는 것.
3. 간단한 피드백을 축적하고 모델 변경 효과를 재현하는 것.
4. 후보 검토량과 CPU 처리 시간을 줄이는 것.

### 1.1 라벨 기준

| 라벨 | 의미 | 학습 사용 |
|---|---|---|
| YES | 구체적인 구성·제어·처리 방법 등 검토할 기술 내용이 있음 | 양성 1 |
| NO | 일정·행정·단순 목표·구체적 수단 없는 성과 소개 등으로 검토 필요성이 낮음 | 음성 0 |
| HOLD | 문맥 부족, 전문 검토 필요, 판단 충돌 등으로 보류 | 이진 학습에서 제외 |
| 미검토 | 사람이 아직 판단하지 않음 | 학습에서 제외 |

예시:

- YES: “Radar confidence가 임계값보다 낮으면 camera feature의 가중치를 증가시키고, 신뢰도가 회복되면 이전 가중치로 복귀한다.”
- NO: “다음 분기에 센서 융합 성능 개선을 추진한다.”
- HOLD: “기존 방법을 개선하였다.” — 주변 문단에 개선 방법이 있는지 확인 필요.

문제·해결수단·효과가 모두 한 문단에 있어야 한다는 조건을 두지 않는다. 기술 내용이 있다는 이유만으로 신규성이 있다고 해석하지 않는다. 부서별 라벨 해석 차이는 라벨 가이드와 검토자 합의로 관리한다.

## 2. 필수 제약조건

아래의 “필수”는 구현 및 검수 조건이다.

| 항목 | 필수 조건 |
|---|---|
| 네트워크 | 설치 완료 후 분석·피드백·학습·평가·내보내기가 인터넷 없이 동작 |
| 외부 API | 외부 AI API, 번역 API, OCR API 및 원격 추론 호출 금지 |
| 연산 장치 | CPU만 사용. CUDA, MPS, 기타 GPU 가속 자동 선택 금지 |
| 메모리 | 물리 RAM 16GB PC에서 운영. 초기 애플리케이션 전체 프로세스 합산 peak RSS 목표 8GiB 이하 |
| 모델 | 검증된 로컬 디렉터리에서만 로드. 파일 누락 시 다운로드하지 않고 명시적 오류 |
| 데이터 | 원문·임베딩·라벨·로그·결과를 회사 PC 또는 승인된 내부 저장소에만 보관 |
| 원본 | 원본 문서는 수정하지 않으며, 결과·주석 문서는 별도 파일로 생성 |
| 재현성 | 모델 revision, 파일 해시, 패키지 버전, 설정, 데이터 snapshot을 기록 |
| 운영 | 단일 PC·단일 운영자 기준으로 시작. 다중 사용자 서버는 후속 범위 |

웹 UI가 필요하면 127.0.0.1에만 바인딩하는 로컬 UI를 사용한다. 이는 외부 API가 아니다. 정적 자원은 함께 배포하며 CDN·외부 폰트·분석 추적·오류 원격 전송을 사용하지 않는다.

초기 설치 파일은 별도의 허용된 환경에서 확보한 후 회사 반입 절차에 따라 옮긴다. 운영 PC가 패키지나 모델을 인터넷에서 받는 절차를 만들지 않는다. 회사의 반입 정책과 라이선스 확인은 배포 준비에 포함한다.

## 3. 기본 아키텍처

```text
PPTX / DOCX / 텍스트 PDF / TXT / MD
  → 원문과 위치를 함께 추출
  → 문단·표·목록 기반 분할 + 주변 문맥 구성
  → 고정 multilingual-E5-small
  → 고정 길이 embedding
  → Logistic Regression
  → 1차 후보 점수 및 마킹
  → 로컬 검토 화면 / HTML·JSONL 결과
  → 사용자 YES / NO / HOLD
  → 검증된 라벨 snapshot
  → classifier 재학습·평가·승격

선택 모듈:
  1차 결과 + 표본 문단 → 고정 Laya-multilingual
                       → shadow 결과 저장
                       → 효과 검증 후 2차 검토 지원
```

### 3.1 구성 요소별 책임

| 요소 | 책임 | 초기 피드백 반영 |
|---|---|---|
| 파서·분할기 | 텍스트·원문 위치·문맥 관계 보존 | 파싱 오류는 코드/규칙 개선 대상으로 기록 |
| E5 | 문단 입력을 embedding으로 변환 | 가중치 변경 없음 |
| classifier | embedding에서 후보 점수 계산 | 검토 확정 라벨로 배치 재학습 |
| 정책 모듈 | threshold와 버전별 규칙으로 표시 결정 | 검증 데이터로 threshold 조정 |
| Laya | 선택적인 구조화된 2차 판단 | 초기에는 추론만, 오류 사례 축적 |
| 리뷰 UI | 원문 확인·피드백·미검출 후보 추가 | 사람의 판단 이벤트 저장 |

이 구조를 SetFit fine-tuning으로 구현하지 않는다. 초기 범위는 **동결된 E5 + 별도 선형 분류기**이다. 사용자가 피드백 대상 모델을 선택할 필요는 없다.

## 4. 입력 파일과 파싱

### 4.1 형식별 범위

| 형식 | 기본 처리 | 위치 정보와 주의사항 |
|---|---|---|
| TXT/MD | UTF-8 우선, 인코딩 실패 시 명시적 재선택 | 행 번호·원문 문자 구간 |
| DOCX | 본문 문단·목록·표를 문서 순서로 추출 | 본문 요소 순번·문단·표/행/셀·run 문자 구간 |
| PPTX | 슬라이드별 텍스트 상자·표·그룹 도형 내부 텍스트 | 슬라이드 번호·shape ID·문단·run·도형 좌표 |
| 텍스트 PDF | 페이지별 문자/단어·좌표 추출 | 페이지·bbox/quad·읽기 순서·회전 정보 |
| 스캔 PDF/이미지 | 기본 단계에서는 미지원 상태를 명시 | 후속 로컬 OCR 모듈과 별도 품질 검수 필요 |

구형 PPT/DOC/HWP, 암호화 문서, 복잡한 수식·차트·도식 내부 의미 해석은 기본 범위에서 제외한다. 미지원 파일은 오류 목록에 남기며 “후보 없음”으로 처리하지 않는다. 발표자 노트·머리말·꼬리말·각주 포함 여부는 설정으로 고정하고 분석 범위를 결과에 표시한다.

### 4.2 파싱 구현 원칙

- 최초 구현 후보: DOCX는 python-docx, PPTX는 python-pptx, PDF는 pdfplumber/pdfminer 계열. 패키지 버전과 라이선스는 반입 manifest에 확정한다.
- DOCX에서 문단 전체와 표 전체를 따로 읽어 뒤에 붙이지 않는다. 원문 블록 순서를 유지한다.
- PPTX의 XML 순서가 사람이 읽는 순서와 다를 수 있다. 좌표 기반 순서와 제목·표 관계를 기록하고 대표 문서로 검수한다.
- PDF 다단 문서, 반복 머리말, 줄바꿈 하이픈을 처리하되 삭제·병합 내역을 기록한다. 읽기 순서가 불확실하면 경고한다.
- 표는 열 제목 + 행 제목 + 셀 내용을 연결한 판정 입력을 만들고, 원문 셀 위치를 별도로 보존한다.
- 텍스트가 거의 없는 페이지와 이미지 영역을 감지하여 OCR 필요 상태를 표시한다. 처리한 페이지 수와 누락·미지원 페이지 수를 함께 보고한다.
- 파일 크기·페이지 수·압축 해제 용량에 제한을 두고 중단 및 재개를 지원한다. 임베디드 매크로·링크·문서 내부 명령은 실행하지 않는다.

## 5. 한국어·영어 혼용과 분할 규칙

### 5.1 텍스트 보존

- 한국어를 영어로 번역하지 않는다. 영어 기술 용어와 한국어 조사가 붙은 형태를 보존한다.
- 대소문자, 변수명, 약어, 단위, 숫자, 비교 연산자, 부정 표현을 보존한다. `mA`, `MA`, `<`, `≥`, “적용하지 않는다”를 임의 정규화하지 않는다.
- 원문 `original_text`와 모델용 `normalized_text`를 구분한다. NFC와 불필요한 공백 정리 정도부터 시작한다.
- 불용어 제거·형태소 원형화·영문 소문자화·자동 약어 치환은 기본적으로 하지 않는다.
- 정규화 후 위치를 원문에 연결할 offset map을 유지한다. 정규화된 문자열 검색만으로 원문 위치를 다시 찾지 않는다.
- 한글 글자 수를 토큰 수로 간주하지 않는다. 실제 모델 tokenizer로 길이를 측정한다.

### 5.2 판정 단위

`paragraph`는 원문 문단 또는 표 행과 같은 논리 단위이며, `segment`는 모델 입력 길이에 맞춘 실제 학습·판정 대상이다.

1. 제목·문단·목록 항목·표 행 경계를 먼저 보존한다.
2. 짧은 불릿은 같은 슬라이드/섹션 안에서 의미가 연결될 때만 묶는다.
3. 긴 문단은 문장 경계로 분할한다. 한국어 종결, 목록 표기, 영어 약어, 소수점에 대한 회귀 사례를 둔다.
4. 한 문장 자체가 길면 tokenizer offset으로 나누고 overlap을 둔다. 원문 구간을 각 segment에 기록한다.
5. 이전/다음 문단은 같은 섹션 안에서 문맥으로만 사용한다. 문맥에 후보가 있다는 이유로 대상 문단을 양성으로 표시하지 않도록 라벨 가이드를 맞춘다.

초기 길이 정책은 E5 전체 입력 512토큰 이하, 대상 본문 약 320토큰, 인접 문맥 합계 약 128토큰, 긴 문장 overlap 약 48토큰이다. 이는 조정 가능한 시작값이다. 접두사·제목·구분자·특수 토큰까지 합산한 실제 길이가 상한을 넘으면 문맥부터 줄이고, 대상 본문은 다시 분할한다. 조용히 뒤를 잘라내지 않는다.

```text
query: [제목] 센서 융합 제어
[이전 문맥] 야간에 카메라 인식 신뢰도가 감소한다.
[판정 대상] Radar confidence에 따라 camera feature의 weight를 조절한다.
[다음 문맥] 야간 평가에서 오검출이 감소했다.
```

마킹과 라벨은 `[판정 대상]`에 귀속한다. 긴 문단의 여러 segment 점수는 초기에는 max로 문단 표시 여부를 정하되, 전체 문단을 읽은 사용자가 문단 수준 YES를 준 경우 모든 하위 segment를 양성으로 복제하지 않는다. 양성 구간을 선택하거나 문단 수준 검토 라벨로 보관하고 학습에서는 제외한다. 중첩 segment는 리뷰 화면에서 중복 표시를 합친다.

## 6. E5 embedding 구현

기본 모델 식별자는 `intfloat/multilingual-e5-small`이다. 운영에서는 모델 ID로 원격 로드하지 않고 revision이 고정된 로컬 경로를 사용한다. 모델 카드 기준 출력은 384차원이며, 분류용 특징에는 `query: ` 접두사를 사용한다. attention mask를 반영한 mean pooling과 L2 정규화를 적용한다. 상한은 512토큰으로 관리한다. [E5 공식 모델 카드](https://huggingface.co/intfloat/multilingual-e5-small)

구현 계약:

```python
class EmbeddingService:
    def encode(self, inputs: list[str]) -> "np.ndarray":
        """CPU float32 [N, 384]. 입력 길이 사전 검증. 가중치 고정."""
```

- 모델은 한 worker에서 한 번만 로드하고 `eval()` 및 inference/no_grad 모드로 실행한다.
- `AutoTokenizer`와 `AutoModel`을 사용하면 mean pooling을 직접 명확하게 구현한다. SentenceTransformer를 선택하면 저장된 pooling 설정을 확인하고 이중 정규화·중복 접두사를 피한다.
- batch size는 8부터 측정하며 긴 입력은 줄인다. 문서 전체 embedding을 한 번에 RAM에 적재하지 않는다.
- embedding cache key에는 모델 파일 해시, tokenizer 해시, 입력 텍스트와 문맥 해시, 접두사, 전처리/분할 버전, 정규화 옵션을 포함한다.
- 캐시는 float32 NPY shard 또는 동급의 단순한 로컬 배열 파일로 저장하고 DB에는 경로·행 번호·해시를 둔다. 초기에는 벡터 DB가 필요 없다.
- 모델이나 전처리 변경 시 해당 cache를 재생성하고 classifier 호환성을 검사한다.

## 7. Classifier와 초기 라벨 부족 대응

### 7.1 기본 모델

기본은 scikit-learn `LogisticRegression`이다. 시작 실험은 `C=1.0`, `class_weight="balanced"`, `max_iter=1000`으로 두고 수렴을 확인한다. 가중치 사용 여부와 C는 검증 데이터에서 비교한다. 확률 배열에서 양성 열은 `classes_`를 확인하여 선택한다.

Logistic Regression은 `fit()`으로 학습하며 클릭마다 온라인 갱신하지 않는다. 스트리밍 규모에서 필요한 경우에만 `SGDClassifier(loss="log_loss")`를 별도 실험한다. 이 경우 `partial_fit`, 초기 classes 지정, 업데이트 순서와 replay snapshot이 필요하다. [LogisticRegression 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)

classifier 점수는 “후보 점수”로 표시한다. `0.82`를 “특허 등록 확률 82%”로 표시하지 않는다. 특히 클래스 가중치나 표본 편향이 있는 모델의 점수는 보정 없이 실제 발생 확률로 해석하지 않는다.

### 7.2 Cold start

- 라벨이 없으면 검토·라벨링 화면부터 제공하고 상태를 `UNTRAINED`로 표시한다.
- 초기에는 기술 분야·문서 형식·작성 시기별 무작위 표본과 규칙 기반 제안 목록을 섞어 사람이 라벨링한다. 규칙 결과를 정답으로 저장하지 않는다.
- 초기 실험 목표는 확정 라벨 200~500개 및 각 클래스 50개 이상이다. 이는 학습 품질을 보장하는 최소치가 아니라 수집 계획이다.
- 한 클래스만 있으면 classifier 학습을 차단한다. 작은 데이터는 그룹 교차검증과 함께 “실험 모델”로 표시한다.
- 상용 배포 판정은 라벨 개수만으로 하지 않고 12절 평가 조건으로 결정한다.

### 7.3 Threshold

`candidate_threshold`는 검증 집합에서 Recall 목표를 만족하는 값 중 검토량을 줄이는 값으로 선택한다. 기본값 0.5나 대화 속 0.6/0.8을 운영 기준으로 고정하지 않는다. 검증 전에는 설정값을 null로 두고 `UNVALIDATED` 상태로 관리한다.

`score >= threshold`이면 후보로 표시한다. 문단 단위 max 집계, 중복 병합, 후단 정책까지 적용한 실제 최종 출력으로 threshold를 검증한다. threshold 변경도 새 정책 버전으로 남긴다.

## 8. 선택 모듈: Laya-multilingual

### 8.1 도입 조건과 한계

Laya는 초기 필수 의존성이 아니다. E5+classifier 완성 후 `convaiinnovations/laya-multilingual`의 로컬 CPU 추론을 검증한다. 공식 프로젝트는 구조화된 choice/score/yes-no 계열 판단을 제공하지만, 한국어 기술 문서의 특허 후보 판별 성능과 대상 PC 처리 속도는 별도 측정해야 한다. 공개 GPU 측정값을 CPU 예상 성능으로 사용하지 않는다. [Laya 공식 저장소](https://github.com/NandhaKishorM/laya), [다국어 모델 카드](https://huggingface.co/convaiinnovations/laya-multilingual)

SDK 버전·checkpoint revision·기본 encoder/tokenizer·모든 부속 파일·라이선스를 확정하고 CPU 오프라인 로드 smoke test를 통과해야 한다. 자동 Router의 다중 모델 선로딩을 사용하지 않고 승인한 다국어 checkpoint만 로드한다. 문맥 상한과 실제 loader API는 선택한 버전에서 확인하며, 이 문서의 adapter는 자체 인터페이스이지 Laya SDK 호출 예제가 아니다.

```python
class SecondStageAdapter:
    def assess(self, target: str, context: str) -> "Assessment":
        """status, raw_scores, decision, model_version,
        question_version, latency_ms, error_code를 반환."""
```

초기 질문 템플릿은 다음 항목을 구조화해 평가한다.

- 대상 구간이 기술적 문제 또는 제약과 관련되는가?
- 대상 구간에 구체적인 구성·처리·제어 방법이 있는가?
- 주변 문맥에 기술적 효과나 차별적 동작이 설명되는가?
- 대상 구간을 사람이 특허 검토 후보로 살펴볼 가치가 있는가?

문제·수단·효과 점수를 임의로 곱하거나 모두 필수 조건으로 묶지 않는다. 원점수, 점수 의미, 클래스 순서와 질문 버전을 보존한다. 임계값과 필요한 calibration은 사람 라벨로 검증한다. 서로 다른 질문 점수는 자동으로 같은 척도라고 가정하지 않는다.

### 8.2 Shadow mode

`laya.mode=shadow`에서는 Laya 결과가 마킹·최종 후보 목록·사용자에게 보이는 순서에 영향을 주면 안 된다. 동일한 1차 prediction ID에 shadow 결과만 추가한다. timeout·실패·대기 상태도 기록하며 1차 결과를 즉시 제공한다.

Laya 처리 대상은 1차 후보 전부 또는 용량에 맞는 표본, 임계값 근처, 1차 비후보 무작위 표본이다. 표본 선택 방식과 포함 확률을 기록한다. 후보만 관찰하면 1차가 놓친 후보를 측정할 수 없으므로 독립 평가 집합에서는 전체를 실행하거나 완전한 평가가 가능한 별도 표본을 구성한다.

### 8.3 활성화 정책

| 모드 | 동작 |
|---|---|
| off | Laya를 로드하지 않음 |
| shadow | 결과 저장만 하며 사용자 출력 불변 |
| assist | 1차 후보는 유지하고 2차 판단·불일치·검토 우선순위를 표시 |
| filter | 충분한 독립 검증 이후에만 1차 후보 중 일부 표시를 억제 |

권장 첫 활성화는 `assist`이다. Laya NO 또는 오류가 자동 탈락으로 이어지지 않는다. `filter`에서도 오류·미실행·낮은 신뢰 결과는 1차 판정을 유지한다. 정상 NO로 제외한 문단은 별도 제외 목록에 남기고 사람이 복구할 수 있어야 한다.

`filter` 승격에는 기준 모델과 동일한 전체 평가 집합에서 최종 Recall 목표 유지, Recall 하락 1%p 이하, 검토량 또는 Precision 개선, CPU·메모리 조건 충족이 필요하다. 이 수치는 제안된 승인 기준이다. 필터 방식에서 1차 탈락 문단은 후단으로 구제할 수 없다는 한계를 명시한다.

## 9. 피드백 및 데이터 모델

기본 저장소는 SQLite이며 migration 버전을 관리한다. embedding과 원문 파일은 별도 저장하고 DB에서 연결한다. 외래키 검사, 쓰기 transaction, 백업·복구를 구현한다. 원문·임베딩도 내부 기술정보로 취급한다.

### 9.1 필수 엔터티

| 테이블 | 주요 필드와 책임 |
|---|---|
| documents | document_id, content_sha256, local_path, format, document_family_id, ingested_at, parse_status, parser_version, coverage_json |
| paragraphs | paragraph_id, document_id, order_index, original_text, source_locator_json, section_id |
| segments | segment_id, paragraph_id, target_original_spans_json, normalized_text, context_segment_ids_json, input_hash, preprocess_version, segmentation_version, quality_flags |
| embeddings | embedding_id, input_hash, encoder_revision, tokenizer_hash, config_hash, file_path, row_index, dimension, dtype, sha256 |
| runs | run_id, config_hash, code_commit, environment_manifest_hash, started_at, status |
| predictions | prediction_id, run_id, segment_id, embedding_id, classifier_version, stage1_score, stage1_decision, threshold, policy_version, final_decision, created_at |
| second_stage_results | result_id, prediction_id, model_revision, question_version, calibration_version, raw_scores_json, decision, status, sampling_reason, inclusion_probability, latency_ms, error_code |
| feedback_events | feedback_id, segment_id 또는 paragraph_id, prediction_id(nullable), label, reason_codes_json, comment, reviewer_id, created_at, supersedes_feedback_id, review_context_hash |
| resolved_labels | target_id, target_type, label, adjudication_status, source_feedback_ids_json, label_guideline_version, resolved_at |
| training_runs | training_run_id, label_snapshot_hash, split_manifest_hash, feature_config_hash, random_seed, metrics_json, artifact_path, status |
| model_registry | model_version, artifact_sha256, training_run_id, encoder_revision, policy_version, promoted_at, retired_at |

### 9.2 식별자·이력 규칙

- 같은 파일의 동일 파서/분할 버전 재실행은 중복 document/segment를 만들지 않도록 결정적 식별 또는 unique 제약을 사용한다.
- 파일 바이트가 바뀌면 새 document version이다. 같은 보고서의 개정본은 document_family_id로 연결한다.
- `source_locator_json`은 형식별 schema를 가진다. PDF는 페이지와 여러 문자 quad, PPTX는 slide/shape/paragraph/run, DOCX는 본문 요소 경로와 run offset을 보존한다.
- prediction은 당시 점수·threshold·버전을 보존한다. 새 모델 분석은 새 prediction으로 저장하며 과거 점수를 덮어쓰지 않는다.
- 모델 점수는 없으면 null이다. 실패나 미실행을 0점·NO로 바꾸지 않는다.
- 피드백 수정은 새 이벤트와 `supersedes_feedback_id`로 남긴다. 여러 검토자의 충돌은 HOLD/합의 대기로 처리한다.
- 같은 검토자의 수정 이력은 최신 유효 이벤트를 쓰되, 다중 검토자 충돌은 단순 최신 작성자 우선으로 해결하지 않는다.
- 피드백을 저장한 prediction ID와 실제 보여준 문맥을 보존한다. 학습에는 원 prediction과 동일한 대상·문맥 embedding을 연결한다.
- 수동 발견 후보는 prediction ID 없이 등록할 수 있다. 원문 구간을 segment로 연결한 후 학습한다.
- 모든 시각은 UTC ISO 8601로 저장하고 UI에서는 현지 시간대로 표시한다.

### 9.3 JSONL 내보내기 예시

아래 값과 ID는 설명용 예시이다. 임베딩 배열 대신 재현 가능한 참조를 저장한다.

```json
{
  "schema_version": 1,
  "document_id": "doc-demo-001",
  "paragraph_id": "para-demo-042",
  "segment_id": "seg-demo-042-01",
  "source_locator": {"format": "pptx", "slide": 7, "shape_id": 12, "paragraph": 2},
  "original_text": "Radar confidence에 따라 camera feature의 weight를 조정한다.",
  "input_hash": "sha256:<target-and-context-hash>",
  "embedding_ref": "embeddings/shard-001.npy#row=42",
  "prediction_id": "pred-demo-001",
  "encoder_revision": "<pinned-revision>",
  "classifier_version": "classifier-0003",
  "stage1_score": 0.73,
  "stage1_threshold": 0.45,
  "stage1_decision": "CANDIDATE",
  "laya": {"mode": "shadow", "status": "ok", "decision": "NO", "score": 0.41},
  "final_decision": "CANDIDATE",
  "feedback": {"label": "YES", "reason_codes": ["CONCRETE_METHOD"], "reviewer_id": "reviewer-01"}
}
```

사유 코드는 `CONCRETE_METHOD`, `GOAL_ONLY`, `ADMIN_CONTENT`, `INSUFFICIENT_CONTEXT`, `PARSING_ERROR`, `WRONG_SPAN`, `DOMAIN_REVIEW_NEEDED`, `OTHER`부터 시작한다. 파싱·위치 오류가 있는 입력은 수정 전 classifier 학습에서 제외한다.

## 10. 모델 업데이트 정책

1. 사용자 클릭은 DB에 즉시 반영한다. 운영 모델은 즉시 바꾸지 않는다.
2. 초기에는 수동 학습 요청 또는 새 유효 라벨 50개 누적을 재학습 제안 조건으로 사용한다. 횟수는 설정으로 조정한다.
3. 학습 시작 시 resolved label·feature·그룹 분할을 immutable snapshot으로 고정한다.
4. E5는 동결하고 train partition의 확정 YES/NO만으로 classifier를 다시 학습한다.
5. validation partition에서 threshold와 필요한 calibration을 선택한다. 최종 test는 학습·threshold 선택에 사용하지 않는다.
6. 현재 모델과 challenger를 같은 평가 기준으로 비교하고 결과를 저장한다.
7. 승인된 후보만 모델 registry에서 원자적으로 승격한다. 진행 중인 분석은 시작 시점 모델 버전을 끝까지 사용한다.
8. 이전 모델+threshold+전처리 설정을 하나의 호환 bundle로 롤백할 수 있게 한다.

임계값 조정, 확률 보정, classifier 학습, E5 fine-tuning, Laya fine-tuning은 서로 다른 실험으로 기록한다. 한 실험에서 여러 축을 동시에 바꾸지 않는 것을 원칙으로 한다.

E5와 Laya의 가중치 학습은 초기·기본 운영 범위 밖이다. 향후 오류 분석에서 필요성이 확인되면 데이터량·CPU 시간·메모리 예산을 별도로 검증한다. 사용자 최종 YES/NO를 Laya의 “문제 있음”, “효과 있음” 등 모든 하위 질문 정답으로 복제하지 않는다.

## 11. Active learning과 불일치 검토

검토 큐는 다음 병렬 범주를 제공한다.

| 범주 | 목적 |
|---|---|
| 1차 임계값 근처 | 현재 표시 경계 개선 |
| classifier와 Laya 불일치 | 각 단계의 오판 원인 확인 |
| 1차 비후보 무작위 표본 | false negative와 선택 편향 확인 |
| 신규 분야·새 표현 | 분포 변화와 미학습 영역 확인 |
| 사용자가 수동 추가한 후보 | 실제 누락 사례 복구 |
| 파싱/문맥 품질 경고 | 모델 문제와 입력 문제 분리 |

초기 리뷰 예산 배분은 경계 사례 30%, 불일치 30%, 비후보 무작위 20%, 다양성 표본 20%를 제안한다. Laya가 off이면 불일치 몫을 무작위와 다양성에 재분배한다. 같은 문서·중첩 문단이 큐를 독점하지 않도록 상한을 둔다.

불확실성은 무조건 0.5 근처로 정의하지 않는다. 실제 후보 threshold까지의 거리와 모델 점수 특성을 기준으로 한다. 불일치는 사람이 검토할 이유이며 어느 한 모델이 정답이라는 증거가 아니다.

Active learning 라벨만으로 모집단 Recall을 보고하지 않는다. 운영 검토 큐와 별도로 전체 문서를 독립적으로 라벨링한 평가 집합을 유지한다. 고정 test 문서는 검토 큐·학습에 유입되지 않도록 제한한다.

## 12. 평가 설계와 지표

### 12.1 분할 및 정답 구축

- 동일 문서의 문단·겹치는 segment·개정본·거의 같은 템플릿은 같은 그룹에 둔다.
- 가능하면 프로젝트/보고서 계열을 기준으로 train/validation/test를 나눈다. 시작 비율은 60/20/20이며 그룹 및 양성 수를 먼저 고려한다.
- 양성이 적어 분할이 불안정하면 그룹 교차검증으로 실험하고 일반화 성능 확정을 보류한다.
- 최근 시기 문서와 새로운 기술 분야를 별도 holdout으로 검토한다.
- 정답 검토자는 후보 목록뿐 아니라 원문 전체를 읽어 양성 구간을 표시한다. 분류기 탈락 구간과 파싱 누락도 평가에 포함한다.
- 중요 표본은 2인 검토 후 불일치를 합의한다. HOLD는 이진 지표에서 제외하되 비율과 이유를 공개한다.

### 12.2 필수 지표

| 지표 | 정의 및 사용 |
|---|---|
| Recall | TP / (TP + FN), 가장 중요한 후보 탐지 지표 |
| Precision | TP / (TP + FP), 검토 효율 |
| F2 | 5PR / (4P + R), Recall에 더 큰 가중 |
| PR-AUC 또는 AP | 임계값 전반 비교. 계산 방식 명시 |
| 검토 비율 | 표시된 고유 대상 수 / 전체 고유 대상 수 |
| 문서 후보 회수율 | 양성이 있는 문서 중 하나 이상 회수한 문서 비율. 구간 Recall의 대체 지표가 아님 |
| 파싱 커버리지 | 전체 페이지·슬라이드·블록 대비 처리·실패·미지원 수 |
| 처리 성능 | cold start, 문서 처리 시간, segment p50/p95, peak RSS |
| Shadow 유효률 | 요청 대비 정상 완료율·지연·누락·timeout 비율 |
| 보정 품질 | 확률로 해석할 때 Brier score/ECE와 reliability 분석 |

Accuracy만으로 모델을 선정하지 않는다. 기술 분야·파일 형식·한국어 중심/혼용·문단 길이·표 여부별 결과와 샘플 수를 함께 제공한다. 분모가 0이면 0 또는 1로 꾸미지 말고 N/A로 기록한다.

### 12.3 누락 계산 원칙

운영 단위 segment 지표와 원문 정답 구간의 end-to-end 회수율을 모두 측정한다. 정답 span의 핵심 해결수단을 표시했는지 평가하고, 단순히 같은 페이지를 표시한 것만으로 TP로 세지 않는다. span overlap 규칙과 중복 병합 기준을 평가 전에 고정한다. OCR/파싱 실패로 후보 구간을 못 읽은 경우 end-to-end에서는 FN으로 남긴다.

필터형 2단계에서는 `전체 Recall = 1차 Recall × 1차 통과 양성의 2차 유지율`이다. 2차 성능만 좋아져도 최종 Recall은 떨어질 수 있다. 1차 단독, shadow에서 시뮬레이션한 후단 정책, 실제 최종 결과를 구분해 보고한다.

### 12.4 제안된 품질 목표

- 대표 독립 평가 집합의 end-to-end Recall 점추정 0.95 이상을 1차 운영 목표로 둔다.
- 양성 수, TP/FN 원수치, 문서 그룹 bootstrap 등으로 구한 95% 신뢰구간을 반드시 함께 보고한다. 작은 표본에서 95% Recall을 보장한다고 표현하지 않는다.
- 최초 확정 평가 수집 목표는 양성 구간 100개 이상, 독립 문서 계열 20개 이상이다. 충족하지 못하면 파일럿 상태로 보고한다. 이 크기만으로 통계적 충분성이 보장되지는 않는다.
- 동일 Recall에서 검토 비율을 줄이는 방향을 선택한다. 검토 용량을 초과하면 threshold를 몰래 높이지 않고 적체·예상 검토량을 표시한다.
- Laya filter는 8.3절 조건을 추가 적용한다. test를 반복 조정에 사용했다면 그 집합을 validation으로 재분류하고 새로운 독립 test를 확보한다.

## 13. UI 및 결과 마킹

최초 제품은 로컬 리뷰 화면과 오프라인 HTML 보고서로 구현한다. 원문 파일 자체의 색칠은 별도 export 기능으로 순차 구현한다.

화면에는 파일명, 슬라이드/페이지/문단 위치, 대상 구간, 앞뒤 문맥, 후보 점수, 검토 상태, 파싱 경고를 표시한다. 사용자는 YES/NO/HOLD, 사유 선택, 메모, 판정 수정, 수동 후보 추가를 할 수 있어야 한다.

- 후보만/전체/미검토/보류/비후보 표본을 전환한다. 낮은 점수 구간도 접근 가능해야 한다.
- 색과 함께 텍스트 상태를 표시하고 키보드로 리뷰할 수 있게 한다.
- shadow 결과는 일반 리뷰 화면에서 숨기고 분석 화면에만 둔다.
- 시스템 판정과 사람 판정을 서로 다른 필드로 표시한다.
- 보고서는 HTML+JSONL을 기본으로 하며 원문에 대응하는 위치 목록을 포함한다. HTML의 문서 텍스트는 escape하고 외부 자원을 로드하지 않는다.
- PDF 주석은 정확한 quad를 지원하는 승인된 라이브러리로 별도 사본에 생성한다. PDF 라이브러리 변경 시 라이선스 확인과 위치 회귀 검증을 수행한다.
- DOCX는 run 경계를 보존한 사본 강조/주석, PPTX는 도형 강조 또는 별도 주석 도형 방식부터 구현한다. 정밀 단어 강조가 불가능하면 도형 단위임을 명시한다.
- 원본과 결과의 파일명을 구분하고 원본 해시 불변을 검사한다. 위치 확신이 없으면 잘못 색칠하지 말고 결과 목록과 경고를 제공한다.

## 14. 구현 구조와 실행 계약

### 14.1 디렉터리 구조

```text
patent-marker/
├── PATENT_MARKING_SYSTEM_SPEC.md
├── README.md
├── pyproject.toml
├── requirements.lock
├── requirements-laya.lock             # 선택 환경, 기본 환경과 분리 가능
├── .gitignore
├── config/
│   └── default.yaml
├── src/patent_marker/
│   ├── cli.py
│   ├── parsers/                      # base, text, docx, pptx, pdf
│   ├── segmentation/                 # normalization, offsets, context
│   ├── embeddings/                   # e5, cache
│   ├── classifiers/                  # train, predict, calibration
│   ├── second_stage/                 # adapter, laya, shadow_queue
│   ├── policies/                     # thresholds, aggregation
│   ├── storage/                      # schema, migrations, repositories
│   ├── feedback/                     # events, resolution, sampling
│   ├── evaluation/                   # splits, metrics, reports
│   ├── export/                       # html, jsonl, annotations
│   └── ui/
├── tests/
│   ├── fixtures/synthetic/           # 비기밀 합성 자료만 Git 포함
│   ├── unit/
│   └── integration/
├── docs/
│   ├── LABELING_GUIDE.md
│   └── OFFLINE_INSTALL.md
├── vendor/wheels/                    # Git 제외, 승인된 설치 패키지
├── models/                          # Git 제외, 로컬 모델 및 registry
├── data/                            # Git 제외, 원문·DB·cache·snapshot
├── artifacts/                       # Git 제외, 학습 모델·평가 보고서
└── outputs/                         # Git 제외, 사용자 분석 결과
```

Git에는 코드·명세·비기밀 설정·합성 테스트만 둔다. 실제 보고자료·DB·임베딩·라벨·로그·모델 가중치·민감한 경로는 넣지 않는다. 내부 데이터 백업은 Git과 별도 운영한다.

### 14.2 기본 설정 예시

```yaml
runtime:
  device: cpu
  cpu_threads: 4
  workers: 1
  batch_size: 8
  offline: true
  process_tree_memory_budget_gib: 8
paths:
  e5: models/multilingual-e5-small
  database: data/patent_marker.sqlite3
segmentation:
  max_input_tokens: 512
  target_tokens: 320
  context_tokens: 128
  overlap_tokens: 48
embedding:
  prefix: "query: "
  normalize: true
classifier:
  type: logistic_regression
  class_weight: balanced
  random_seed: 42
policy:
  candidate_threshold: null
  status: UNVALIDATED
laya:
  mode: off
  local_path: null
  timeout_seconds: 30
ui:
  host: 127.0.0.1
  port: 8765
```

위 숫자는 시작 설정이다. 메모리 budget은 설정만으로 강제되지 않으므로 실제 process tree를 측정하고 batch 축소·worker 제한·중단 정책을 구현해야 한다. 모델 경로는 설정 기준 디렉터리에 대해 resolve하며 원격 ID/URL을 거부한다.

### 14.3 제안 CLI

아래는 구현할 CLI 계약이며 현재 존재하는 프로그램 사용법이 아니다.

```bash
python -m patent_marker.cli doctor --offline
python -m patent_marker.cli ingest --input ./data/inbox --config ./config/default.yaml
python -m patent_marker.cli review --host 127.0.0.1
python -m patent_marker.cli train --snapshot ./data/snapshots/labels-v1.jsonl
python -m patent_marker.cli evaluate --model classifier-0001 --split validation-v1
python -m patent_marker.cli promote --model classifier-0001 --report ./artifacts/eval-0001.json
python -m patent_marker.cli analyze --input ./data/inbox --output ./outputs/run-001
python -m patent_marker.cli export --run run-001 --format html,jsonl
python -m patent_marker.cli rollback --model classifier-0000
```

`doctor`는 로컬 모델·tokenizer·파일 해시·쓰기 권한·CPU 장치·의존성·DB 버전을 확인한다. `promote`는 평가 기준 미충족 모델을 자동 차단한다. 테스트 누수나 미검증 threshold를 통과로 처리하지 않는다. 모든 긴 작업은 진행량·실패 목록·중단과 재개를 지원한다.

## 15. 오프라인 설치와 자원 관리

### 15.1 배포 준비

1. 실제 OS·CPU 아키텍처·Python 버전을 기록한다. 초기 후보는 Windows 10/11 x86-64와 Python 3.11이며, 사용 가능 여부는 회사 환경에서 확정한다.
2. 대상 OS/Python에 맞는 CPU 패키지 wheel과 전이 의존성을 모두 확보하고 버전을 잠근다. Laya와 기본 스택의 의존성이 충돌하면 별도 CPU worker 환경으로 분리한다.
3. 모델·tokenizer·설정·필요한 부속 모델 파일과 라이선스를 함께 반입한다. 파일마다 SHA-256과 출처·revision을 manifest에 기록한다.
4. 패키지는 `--no-index --find-links`와 검증된 lock을 사용해 설치한다. wheel이 없는 의존성을 운영 PC에서 네트워크 빌드하지 않는다.
5. 깨끗한 캐시와 네트워크 차단 환경에서 설치 및 최초 실행을 재현한다.

예시 설치 명령은 아래와 같다. `requirements.lock`은 hash가 포함된 반입용 pip lock으로 생성해야 한다.

```bash
python -m pip install --no-index --find-links ./vendor/wheels --require-hashes -r requirements.lock
```

모델 import/load 전에 `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`을 설정하고 지원되는 loader에 `local_files_only=True`를 전달한다. `trust_remote_code`는 기본 false로 유지한다. 별도 코드가 꼭 필요한 모델은 반입한 코드를 검토하고 로컬 패키지로 고정한다. 환경 변수만으로 전체 앱의 오프라인을 보장하지 않으므로 네트워크 차단 통합시험을 실시한다. [Hugging Face 오프라인 실행 안내](https://huggingface.co/docs/transformers/installation#offline-mode)

### 15.2 CPU·메모리·장애

- 기본 추론 worker 1개, CPU thread 4개부터 측정한다. 시스템 사용성을 고려하여 조정한다.
- 파싱·embedding·DB 저장을 bounded queue로 연결한다. 긴 문서는 페이지/블록 단위로 스트리밍한다.
- 학습과 대량 분석을 동시에 실행하지 않는 것을 기본으로 한다. Laya는 별도 작업 큐에서 실행하며 필요하면 E5와 순차 로드한다.
- Laya timeout은 실제 worker 취소/재시작으로 자원을 회수한다. 응답만 timeout 처리하고 계속 계산하는 작업을 무제한 쌓지 않는다.
- ONNX/INT8은 후속 최적화이며 원본 대비 embedding 차이뿐 아니라 최종 Recall·표시 변화도 재평가한다.
- DB와 artifact는 임시 파일 기록 후 원자적 교체를 사용하고, 중단 후 불완전 shard를 정상 cache로 사용하지 않는다.
- 로그에는 기본적으로 문서 본문을 넣지 않는다. 필요 시 내부 디버그 모드로 제한하고 보존 기간을 설정한다.
- 실제 문서·라벨을 외부 AI 코딩 도구에 전달하지 않는다. 구현에 필요한 예시는 합성 자료를 사용한다.

## 16. 개발 단계와 단계별 완료 조건

| 단계 | 구현 범위 | 종료 조건 |
|---|---|---|
| Phase 0 | 환경 조사, 라벨 가이드, 반입 manifest, 합성 fixture | CPU 오프라인 모델 로드 및 네트워크 차단 시험 통과 |
| Phase 1A | TXT/MD/DOCX/PPTX/텍스트 PDF 추출·위치·분할, 리뷰·DB | 대표 파일별 위치 검수, unsupported/partial 상태, 라벨 저장·수정·재실행 확인 |
| Phase 1B | 고정 E5, cache, Logistic Regression, HTML/JSONL 마킹 | 라벨 수집→학습→독립 평가→배포·롤백까지 연결, Recall 결과 보고 |
| Phase 2 | active learning, classifier 재학습, 이력 및 지표 | 미검출 후보 수동 추가, 비후보 표본 검토, 모델 비교·rollback 통과 |
| Phase 3 | Laya adapter와 shadow | 출력 불변 시험, 대표 데이터 비교, 시간·메모리·실패율 보고 |
| Phase 4 | 검증된 assist, 선택적 filter, 원문 사본 주석 | 최종 Recall 유지 및 형식별 주석 위치·서식 검증 |
| Phase 5 | 필요 시 OCR·성능 최적화·모델 학습 연구 | 별도 범위·실험 결과에 근거하여 채택 |

Phase 1~2만으로 운영 가능한 제품을 만든다. Laya 설치·품질 문제가 기본 제품 완료를 막지 않도록 한다. 원본 PPTX/DOCX/PDF 사본 내 마킹이 업무 필수라면 해당 형식의 Phase 4 export 완료까지 배포 범위에 포함한다.

## 17. 검수 체크리스트

### 17.1 필수 기능·회귀 시험

- [ ] 네트워크 차단 및 비어 있는 사용자 모델 cache에서 로컬 반입 파일만으로 설치·분석·재학습·export가 된다.
- [ ] 모델 누락 시 즉시 오류를 표시하고 DNS/HTTP 다운로드를 시도하지 않는다.
- [ ] GPU가 있는 장비에서도 실제 모델 tensor와 실행 backend가 CPU임을 확인한다.
- [ ] 한국어·영어 혼용, 단위·부정문·목록·표·긴 문단·동일 문자열 반복의 위치가 보존된다.
- [ ] 512토큰 초과 내용은 분할되며 대상 본문을 조용히 잘라내지 않는다.
- [ ] PPTX 그룹/표, DOCX 문단-표 혼재, PDF 다단/회전/스캔 자료의 처리 또는 경고가 명확하다.
- [ ] 원본 해시는 처리 전후 같고 모든 출력은 별도 파일이다.
- [ ] YES/NO/HOLD와 수정 이력을 보존하며 미검토/HOLD/파싱 오류는 이진 학습에 섞이지 않는다.
- [ ] 같은 문서 계열과 중첩 segment가 train/test에 동시에 들어가지 않는다.
- [ ] 모델·threshold·문맥·전처리·피드백 snapshot으로 과거 판정을 재현할 수 있다.
- [ ] shadow on/off에서 사용자 마킹과 표시 순서가 같으며 Laya 실패가 1차 출력을 막지 않는다.
- [ ] classifier 승격과 rollback 시 bundle 호환성을 검사하고 진행 중 run의 버전이 바뀌지 않는다.
- [ ] 분석 중단·재시작이 DB 중복 및 손상된 cache를 만들지 않는다.

### 17.2 품질·성능 검수

- [ ] 12절의 Recall·Precision·F2·검토 비율·FN 목록·신뢰구간·표본 수를 보고한다.
- [ ] end-to-end Recall 0.95 목표 미달이면 원인을 기록하고 파일럿 상태로 남긴다.
- [ ] 16GB 대상 PC에서 모든 하위 프로세스를 포함한 peak RSS 8GiB 이하 목표와 정상 운영을 확인한다.
- [ ] 초기 성능 시험은 텍스트 기반 100페이지 상당/약 1,000 segment의 고정 fixture, 길이 분포, CPU 사양을 기록한다. 1차 분석 warm 실행 10분 이내를 제안 목표로 측정하고, cold start와 Laya 비용은 별도 보고한다.
- [ ] 실제 업무 최대 크기 문서에서도 RAM 고갈·무한 대기 없이 완료하거나 명확한 제한 안내 후 재개할 수 있다.
- [ ] 사본 주석 기능을 제공한다면 각 지원 형식의 반복 문자열·줄바꿈·표·서식 사례를 열어서 검수한다.

목표 미달 값을 임의 변경하여 통과시키지 않는다. 변경 사유·새 예산·영향을 명세와 평가 보고서에 함께 기록한다.

## 18. 위험, 가정, 확인할 사항

| 항목 | 현재 가정/위험 | 대응 |
|---|---|---|
| CPU·OS | 회사 PC의 정확한 사양 미확정 | Phase 0에서 사양·권한·지원 Python 확정 |
| 라벨 부족 | E5만으로 후보 기준이 학습되어 있지 않음 | cold start 라벨링과 독립 평가부터 시작 |
| 한국어 혼용 | 다국어 지원이 해당 업무 성능을 보장하지 않음 | 사내 혼용 텍스트 slice와 누락 사례 평가 |
| 파싱 | 도표·스캔·읽기 순서로 핵심 수단 누락 가능 | 커버리지 경고, 원문 대비 검수, OCR 별도 단계 |
| 선택 편향 | 표시된 후보만 라벨링하면 누락을 보지 못함 | 비후보 표본과 전체 문서 정답 평가 유지 |
| 기준 변화 | 검토자/부서/시기별 후보 정의가 다를 수 있음 | 라벨 가이드 버전·합의 기록·시기별 평가 |
| Laya | SDK·질문 형식·성능·자원 요구 변화 가능 | revision 고정, adapter 격리, shadow 검증, 기본 off |
| 후단 필터 | Precision 개선과 함께 Recall 저하 가능 | assist 우선, filter 별도 승격 기준 |
| 수치 해석 | 후보 점수와 등록 확률 혼동 | UI 명칭 고정, 필요 시 calibration |
| 중복 데이터 | 개정본·템플릿 반복으로 평가 과대 추정 | 문서 계열 그룹 분할과 근접 중복 탐지 |
| 공급·의존성 | 모델 파일 누락·OS wheel 불일치·라이선스 제약 | 사전 반입 manifest와 깨끗한 환경 설치 시험 |
| 마킹 수준 | 논리 구간과 원본 파일 단어 강조의 난이도 차이 | MVP HTML+위치 목록, 사본 export는 형식별 검수 |

## 19. AI 코딩 도구에 전달할 작업 지시

아래 지시와 이 문서 전체를 함께 전달한다.

```text
PATENT_MARKING_SYSTEM_SPEC.md를 구현 기준으로 사용하라.
기존 저장소 구조와 실행 환경을 먼저 확인하고 필요한 파일을 작성하라.

우선 Phase 0, 1A, 1B를 완성하라.
1. 환경·의존성·로컬 모델 경로 검증
2. 원문 위치를 보존하는 파서와 분할기
3. SQLite schema/migration과 피드백 이력
4. 동결 E5 embedding과 캐시
5. 수동 라벨링, Logistic Regression 학습, 그룹 평가
6. threshold 선택·승격·롤백
7. 로컬 리뷰 화면과 HTML/JSONL 마킹 결과
8. 네트워크 차단·CPU·위치·라벨 누수 방지 회귀 시험

실제 API는 반입한 패키지 버전의 문서를 확인하여 구현하라.
Laya는 optional adapter와 off 설정까지만 두고, 기본 제품 완성 후 shadow를 구현하라.
E5/Laya fine-tuning, 원격 API, 자동 다운로드, GPU 자동 선택을 추가하지 마라.
사용자의 피드백 한 건으로 운영 모델을 자동 교체하지 마라.
원문·라벨·모델 가중치를 Git에 넣지 마라.
학습 라벨이 없으면 UNTRAINED 리뷰 모드로 동작하게 하라.
미구현·미검증 기능은 명확히 표시하고 측정하지 않은 성능을 주장하지 마라.

완료 시 실행 방법, 오프라인 반입 목록, 검수 결과,
실제 지원 파일 범위, 남은 제한을 README에 기록하라.
```

## 20. 참고 자료 및 명세 해석

아래는 2026-10-04 확인한 기술 참고 자료다. 운영 시스템이 이 링크에 접속할 필요는 없다. 배포 시 선택한 revision의 문서와 라이선스를 함께 보관한다.

- [multilingual-E5-small 공식 모델 카드](https://huggingface.co/intfloat/multilingual-e5-small): embedding 차원·입력 접두사·pooling·길이 제한.
- [Laya 공식 소스](https://github.com/NandhaKishorM/laya), [Laya-multilingual 공식 모델 카드](https://huggingface.co/convaiinnovations/laya-multilingual): 선택 모듈의 실제 API·checkpoint 검증 출발점.
- [scikit-learn LogisticRegression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html): classifier 구현 API.
- [Transformers 설치 및 오프라인 사용](https://huggingface.co/docs/transformers/installation#offline-mode): 로컬 모델 로드 방식.

아키텍처·데이터 모델·단계 구분·평가 목표·자원 예산은 이 프로젝트를 위한 설계 결정이다. 앞선 대화에서 제시한 점수·속도·메모리 적합성 예시는 실제 검증 결과로 간주하지 않는다.

# 오프라인 설치와 반입 절차

운영 PC는 인터넷에서 패키지나 모델을 받지 않는다. 필요한 파일은 인터넷이 허용된 **준비 환경**에서 확보한 뒤 회사 반입 절차로 옮긴다. 운영 코드에는 다운로드 경로가 없다.

> 현재 상태: 아래 절차 중 "운영 PC" 부분은 대상 Windows PC에서 실행해 보지 않았다. 개발 검증은 macOS arm64 / Python 3.11.15에서 같은 패키지 버전으로 했다. Phase 0 환경 조사에서 OS·CPU·Python 버전·설치 권한을 먼저 확정한다.

## 1. 반입 목록

| 종류 | 내용 | 확인 방법 |
|---|---|---|
| 저장소 | 이 Git 저장소 (코드, 설정, 합성 시험 자료, 문서) | commit hash |
| 설치 패키지 | `requirements.lock` 에 적힌 wheel 28개 (대상 OS·Python용) | lock의 sha256, `vendor/BUNDLE_MANIFEST.json` |
| 모델 | `models/multilingual-e5-small/` 의 `model.onnx`, `tokenizer.json`, `config.json`, `MODEL_CARD.md`, `manifest.json` | manifest의 sha256 |
| 문서 | 이 문서, `README.md`, `docs/LABELING_GUIDE.md`, 스펙 | - |

모델 파일 (intfloat/multilingual-e5-small, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, MIT):

| 파일 | 크기 | sha256 |
|---|---|---|
| `model.onnx` | 470,268,510 | `ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665` |
| `tokenizer.json` | 17,082,730 | `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39` |
| `config.json` | 653 | `bbb7c1333fc4b3e27fbc9cd5d2070aabcc1d4dfb99917c3633e772f97545a6b6` |
| `MODEL_CARD.md` | 497,538 | `0038de97aee16258cecbad7ffda4b4febd6953e747a00e0ddbc8e6ed241e9c1c` |

`requirements.lock` 에는 torch, transformers, huggingface-hub, requests가 없다. tokenizers가 huggingface-hub를 의존성으로 선언하지만 로컬 `tokenizer.json` 을 읽는 데는 필요 없어서 `--no-deps` 로 설치한다.

각 패키지와 모델의 라이선스는 회사 정책에 따라 반입 전에 확인한다. 주요 항목: onnxruntime(MIT), tokenizers(Apache-2.0), scikit-learn(BSD-3), numpy(BSD-3), python-docx(MIT), python-pptx(MIT), pdfplumber(MIT), pdfminer.six(MIT), pypdf(BSD-3), pypdfium2(Apache-2.0/BSD-3), PyYAML(MIT), psutil(BSD-3). 전이 의존성까지의 전체 목록은 lock 파일을 기준으로 다시 확인해야 한다.

## 2. 준비 환경에서 할 일 (인터넷 허용)

```bash
python tools/fetch_model.py --dest models/multilingual-e5-small
```

고정 revision의 파일을 받고 Hub 메타데이터의 해시와 대조한 뒤 `manifest.json` 을 만든다.

```bash
python tools/prepare_offline_bundle.py --platform win_amd64 --python-version 3.11
```

lock에 적힌 wheel을 `vendor/wheels/` 에 받고 `vendor/BUNDLE_MANIFEST.json` 을 만든다. 대상이 Windows x86-64 / Python 3.11이 아니면 lock부터 다시 만든다:

```bash
uv pip compile requirements/base.in --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes -o base.lock
```

```bash
uv pip compile requirements/tokenizers.in --no-deps --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes -o tokenizers.lock
```

두 결과를 이어 붙인 것이 `requirements.lock` 이다. Linux 대상이면 PyPI의 torch가 필요 없으므로 같은 방법을 쓰되 플랫폼만 바꾼다.

## 3. 운영 PC에서 할 일 (인터넷 없음)

Windows PowerShell 기준이다.

```powershell
py -3.11 -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install --no-index --find-links .\vendor\wheels --require-hashes --no-deps -r requirements.lock
```

```powershell
$env:PYTHONPATH = "src"
```

패키지로 설치하지 않고 `PYTHONPATH=src` 로 실행한다. 설치가 필요하면 `pip install --no-index --no-deps --no-build-isolation -e .` 를 쓴다(venv에 setuptools가 있어야 한다).

```powershell
.venv\Scripts\python -m patent_marker.cli doctor --offline
```

모든 항목이 `[ OK ]` 여야 한다. 점검 내용: Python·패키지, 다운로드 가능 라이브러리 미설치, 모델 파일 해시, CPU 추론, 네트워크 가드, DB, 쓰기 권한, 메모리·CPU.

onnxruntime은 Windows에서 Visual C++ 재배포 패키지가 필요하다. `import onnxruntime` 이 DLL 오류로 실패하면 그 패키지의 반입 여부를 확인한다.

## 4. 네트워크 차단 확인

1. `config/default.yaml` 의 `runtime.offline` 이 `true` 인지 확인한다. 이 값이 true이면 프로그램이 루프백 외 접속과 DNS 조회를 스스로 차단하고, 시도가 있으면 오류로 멈춘다.
2. PC의 네트워크를 끊거나 방화벽으로 막은 상태에서 `doctor --offline`, `analyze`, `review` 를 실행해 본다.
3. 모델 디렉터리 이름을 잠시 바꾼 뒤 `analyze` 를 실행해 "자동으로 내려받지 않습니다" 오류가 바로 나오는지 확인한다.

프로그램 안의 가드는 Python 소켓 수준이다. 최종 보장은 OS·방화벽 수준 차단이다.

## 5. 시험 실행 (선택)

pytest와 reportlab을 함께 반입한 경우:

```powershell
.venv\Scripts\python -m pytest
```

모델이 없으면 실제 모델 시험 6개는 사유와 함께 건너뛴다.

## 6. 데이터 위치와 백업

| 경로 | 내용 | Git |
|---|---|---|
| `data/patent_marker.sqlite3` | 문서 텍스트, 위치, 예측, 피드백, registry | 제외 |
| `data/embeddings/` | 임베딩 캐시 (NPY) | 제외 |
| `data/snapshots/` | 라벨 snapshot | 제외 |
| `artifacts/` | 분류기 bundle, 평가 보고서 | 제외 |
| `outputs/` | 분석 결과물 | 제외 |

원문 텍스트와 임베딩도 내부 기술정보로 취급한다. 백업은 `backup` 명령으로 DB 사본을 만들고 `data/embeddings`, `artifacts` 를 함께 복사한다. Git에는 넣지 않는다.

## 7. 모델이나 전처리를 바꿀 때

모델 파일, tokenizer, 접두사, 전처리·분할 버전이 바뀌면 임베딩 캐시가 자동으로 분리되고 기존 분류기는 호환되지 않는 것으로 판정되어 로드가 거부된다. 새 구성으로 다시 학습·평가·승격해야 한다.

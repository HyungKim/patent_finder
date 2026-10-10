# 오프라인 설치와 반입 절차

운영 PC는 인터넷에서 패키지나 모델을 받지 않는다. 필요한 파일은 인터넷이 허용된 **준비 환경**에서 확보한 뒤 회사 반입 절차로 옮긴다. 운영 코드에는 다운로드 경로가 없다.

> 현재 상태(2026-10-10): `setup.bat` · `mark.bat` · `run.bat` · `train.bat` 과 3.3의 직접 명령 절차를 GitHub의 Windows 러너(영문 Windows Server)에서 Python 3.11, 3.12, 3.13 각각으로 확인했다(`.github/workflows/windows.yml`). 묶음의 wheel만으로 설치되고, 점검과 합성 자료 분석이 되며, 시험 175개가 세 버전에서 통과했다. 릴리스 v0.1.5의 zip으로는 내려받아 설치, 폴더 이동, 묶음 전체로 바꾸기, v0.1.4를 설치한 위에 code-only zip을 덮어쓴 뒤 `train.bat` 까지 확인했다. 대상 회사 PC(한국어 Windows 10/11)에서는 아직 확인하지 않았다.

## 1. 반입 목록

| 종류 | 내용 | 확인 방법 |
|---|---|---|
| 저장소 | 이 Git 저장소 (코드, 설정, 합성 시험 자료, 문서) | commit hash |
| 설치 패키지 | `requirements.lock` 에 적힌 패키지 28개의 wheel. Windows x86-64의 Python 3.11·3.12·3.13용을 함께 넣으면 파일 46개, 270 MB | lock의 sha256, `vendor/BUNDLE_MANIFEST.json` |
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
python tools/prepare_offline_bundle.py --platform win_amd64 --python-version 3.11 3.12 3.13
```

lock에 적힌 wheel을 `vendor/wheels/` 에 받고 `vendor/BUNDLE_MANIFEST.json` 을 만든다. 이 도구는 `pip download` 를 쓰므로 pip이 들어 있는 Python으로 실행한다. `uv venv` 로 만든 환경에는 pip이 없고, `python -m venv` 로 만든 환경에는 있다. 대상이 Windows x86-64 / Python 3.11이 아니면 lock부터 다시 만든다:

```bash
uv pip compile requirements/base.in --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes -o base.lock
```

```bash
uv pip compile requirements/tokenizers.in --no-deps --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes -o tokenizers.lock
```

두 결과를 이어 붙인 것이 `requirements.lock` 이다. Linux 대상이면 PyPI의 torch가 필요 없으므로 같은 방법을 쓰되 플랫폼만 바꾼다.

## 3. 운영 PC에서 할 일 (인터넷 없음)

설치 묶음(zip)을 풀어 나온 `patent_finder` 폴더를 원하는 곳에 두고 `setup.bat` 을 더블클릭한다. 아래는 폴더를 `D:\patent_finder` 에 둔 예다.

| 파일 | 하는 일 |
|---|---|
| `setup.bat` | 설치. Python 3.11~3.13(64비트)을 찾아 `.venv` 를 만들고, 묶음의 wheel만으로 설치한 뒤 점검한다. 다시 실행해도 된다 |
| `mark.bat` | 분석. 더블클릭하면 파일 열기 창이 뜨고, 고른 파일을 분석한다(Ctrl·Shift로 여러 개). 파일이나 폴더를 이 아이콘 위에 끌어다 놓아도 된다. 결과는 `outputs\mark-날짜-시각` 에 생기고, 끝나면 그 폴더가 열린다 |
| `run.bat` | 검토 화면. 브라우저로 `http://127.0.0.1:8765` 를 연다. 검은 창을 닫으면 끝난다 |
| `train.bat` | 판정으로 다시 학습. snapshot → 학습 → 평가를 하고 지금 모델과 비교한 표를 보여 준 뒤, 새 모델을 쓸지 묻는다. `y` 와 Enter를 쳐야 다음 분석부터 새 모델을 쓴다. 판정은 저절로 학습되지 않는다. YES와 NO 판정이 모두 있어야 하고, 학습에 쓰지 않은 문서의 판정이 있어야 평가할 수 있다. 모자라면 이유를 보여 주고 멈춘다 |

파일 열기 창은 Python에 들어 있는 부품(tkinter)으로 띄운다. 이 부품이 빠진 Python이면 안내문이 나오고, 그때는 끌어다 놓는 방법을 쓴다. 같은 내용을 짧게 적은 안내문이 폴더의 `★먼저읽기_Windows_설치순서.txt` 다. `setup.bat` 이 하는 일을 직접 명령으로 하려면 3.3을 따른다.

### 3.1 설치 위치

프로그램 폴더는 어느 드라이브에 두어도 된다. 설치 패키지(`.venv`), 모델, DB, 결과물이 모두 이 폴더 아래에 생기고, PC에 이미 설치된 Python은 실행 파일로만 쓴다. Python이 C 드라이브에 있어도 폴더를 D 드라이브에 두면 C 드라이브는 거의 쓰지 않는다. Windows에 따로 등록하는 것이 없으므로 지울 때는 폴더만 지우면 된다.

- 설치한 뒤에 폴더를 다른 곳으로 옮겨도 그대로 쓸 수 있다(Windows 러너에서 확인). 옮긴 뒤 실행되지 않거나 PC의 Python을 지우고 다른 버전으로 바꿨으면 `setup.bat` 을 다시 실행한다.
- 경로는 짧은 영문으로 한다.
- 준비 환경의 `.venv`, `data`, `artifacts`, `outputs` 는 가져오지 않는다. `.venv` 는 OS가 달라 쓸 수 없고, 나머지는 준비 환경에서 실행한 흔적이다.
- 필요한 공간: 모델 약 490 MB, 반입 wheel 약 270 MB(세 버전용. 설치 뒤에는 지워도 된다), 설치된 패키지 수백 MB(Windows에서는 측정하지 않았다. macOS에서는 약 300 MB), 그리고 쓰면서 쌓이는 DB·임베딩·결과물.

### 3.2 Python 확인

```powershell
py -0p
```

3.11, 3.12, 3.13 중 하나가 있고 `-32` 가 붙어 있지 않아야 한다(64비트). 설치 묶음의 `vendor/wheels` 에는 세 버전용 wheel이 함께 들어 있다. `setup.bat` 은 3.11, 3.12, 3.13 순서로 찾아 처음 발견한 것을 쓴다. 특정 버전을 쓰게 하려면 검은 창에서 `set PM_PYTHON=py -3.12` 를 입력한 뒤 `setup.bat` 을 실행한다. Python 3.10 이하와 3.14 이상은 지원하지 않는다.

### 3.3 직접 명령으로 설치 (setup.bat을 쓰지 않을 때)

Windows PowerShell 기준이다. 모든 명령은 프로그램 폴더에서 실행한다.

```powershell
cd D:\patent_finder
```

```powershell
py -3.11 -m venv .venv
```

3.12나 3.13을 쓰려면 숫자만 바꾼다. `py` 가 없으면 `python.exe` 의 전체 경로로 실행한다. 예: `& "C:\Python311\python.exe" -m venv .venv`

```powershell
.venv\Scripts\python -m pip install --no-index --find-links .\vendor\wheels --require-hashes --no-deps --no-cache-dir -r requirements.lock
```

이 명령이 `UnicodeDecodeError` 로 끝나면 `requirements.lock` 에 ASCII가 아닌 문자가 들어 있는 것이다. Python 3.11에 들어 있는 pip은 이 파일을 Windows 시스템 코드 페이지로 읽는다. 0.1.0 묶음이 이 문제로 설치되지 않았고 0.1.1에서 고쳤다.

`--no-cache-dir` 는 pip이 사용자 폴더(보통 C 드라이브)에 캐시를 만들지 않게 한다. C 드라이브에 공간이 거의 없어 설치 중 임시 파일까지 다른 드라이브에 두려면, 위 명령보다 먼저 아래를 실행한다. 설치가 끝나면 `.tmp` 폴더는 지워도 된다.

```powershell
mkdir .tmp; $env:TEMP = "$PWD\.tmp"; $env:TMP = $env:TEMP
```

```powershell
$env:PYTHONPATH = "src"
```

패키지로 설치하지 않고 `PYTHONPATH=src` 로 실행한다. 이 설정은 PowerShell 창을 닫으면 사라지므로, 창을 새로 열 때마다 프로그램 폴더로 이동한 뒤 다시 실행한다. `pip install -e .` 는 쓰지 않는다. 인터넷이 없으면 빌드 도구를 받지 못해 실패할 수 있다.

```powershell
.venv\Scripts\python -m patent_marker.cli doctor --offline
```

모든 항목이 `[ OK ]` 여야 한다. 점검 내용: Python·패키지, 다운로드 가능 라이브러리 미설치, 모델 파일 해시, CPU 추론, 네트워크 가드, DB, 쓰기 권한, 메모리·CPU.

onnxruntime은 Windows에서 Visual C++ 재배포 패키지가 필요하다. `import onnxruntime` 이 DLL 오류로 실패하면 그 패키지의 반입 여부를 확인한다.

### 3.4 새 버전으로 바꿀 때

릴리스에는 두 가지 zip이 있다.

| 파일 | 들어 있는 것 | 쓰는 경우 |
|---|---|---|
| `patent_finder-x.y.z-windows-x64.zip` | 프로그램, 설치 패키지(wheel), 모델 | 처음 설치할 때, 설치 패키지나 모델이 바뀐 버전으로 갈 때 |
| `patent_finder-x.y.z-code-only.zip` | 프로그램 파일만 (`src`, batch 파일, `tools`, `tests`, 문서). `config`, 설치 패키지, 모델은 없다 | 코드만 바뀐 버전으로 갈 때. 릴리스 설명에 "코드만 바꾸기 가능"이라고 적힌 경우 |

**코드만 바꾸기 (code-only zip)**: 검은 창(`run.bat`, `mark.bat`)을 모두 닫고, zip을 풀어 나온 `patent_finder` 폴더를 기존 폴더가 있는 곳(예: `D:\`)에 붙여 넣는다. 탐색기가 "폴더 병합"을 물으면 병합하고, "대상 폴더의 파일 바꾸기"를 고른다. `setup.bat` 은 다시 실행하지 않아도 된다. 판정 기록(`data`), 학습한 분류기(`artifacts`), 결과(`outputs`), 설정(`config`)은 그대로 남는다. 끝나면 `mark.bat` 으로 한 번 분석해 본다. `train.bat` 처럼 새로 생긴 파일은 이 방법으로 들어온다.

**설치 묶음 전체로 바꾸기**: 기존 폴더에 덮어쓰지 않고, 새 폴더에 설치한 뒤 기록만 옮긴다. 예전 버전의 파일이 섞여 남지 않고, 새 버전이 잘 도는 것을 본 뒤에 예전 것을 지울 수 있다.

1. 실행 중인 검은 창(`run.bat`, `mark.bat`)을 모두 닫고, 기존 `D:\patent_finder` 폴더의 이름을 `patent_finder_old` 로 바꾼다.
2. 새 설치 묶음(zip)을 풀어 `patent_finder` 폴더를 `D:\` 에 둔다.
3. 지금까지의 판정 기록과 결과를 이어 쓰려면 `patent_finder_old` 안의 `data`, `artifacts`, `outputs` 세 폴더를 새 `D:\patent_finder` 안으로 옮긴다. 처음부터 새로 시작하려면 옮기지 않는다.
4. 새 폴더의 `setup.bat` 을 더블클릭한다.
5. `mark.bat` 으로 한 번 분석해 보고, 잘 되면 `patent_finder_old` 폴더를 지운다.

세 폴더에 들어 있는 것: `data` 는 문서 기록·판정·임베딩 캐시, `artifacts` 는 학습한 분류기와 평가 보고서, `outputs` 는 분석 결과물이다. 나머지(`src`, `vendor`, `models`, `.venv` 등)는 프로그램이라 새 묶음의 것을 쓴다. `config\default.yaml` 을 직접 고쳐 썼다면 같은 내용을 새 폴더의 파일에 다시 적는다.

### 3.5 지울 때

`D:\patent_finder` 폴더를 지우면 끝이다. Windows에 따로 등록하는 것이 없어서 시작 메뉴, 레지스트리, 사용자 폴더에 남는 것이 없고, PC의 Python도 그대로다.

- 지우기 전에 검은 창을 모두 닫고, 결과 폴더의 PPTX·PDF를 열어 둔 PowerPoint 같은 프로그램도 닫는다. 열려 있으면 Windows가 사용 중이라며 지우지 못한다.
- 폴더를 지우면 판정 기록(`data`), 학습한 분류기(`artifacts`), 분석 결과(`outputs`)도 함께 사라진다. 남기려면 이 세 폴더를 먼저 다른 곳에 복사해 둔다.

### 3.6 판정 단위를 잘게 (세분 모드)

기본 판정 단위는 불릿 묶음(상위 + 바로 아래 하위 불릿)·문단·표 행이다. 설정 `segmentation.unit: fine` 으로 바꾸면 불릿마다, 문장마다, 표는 셀마다 따로 판정하고 그 조각만 표시한다. 단위가 다르면 문서를 다시 나누고 분류기도 따로 만들어지며 판정 기록도 단위마다 따로 쌓이므로, **기본 단위와 함께 쓰려면 폴더를 따로 둔다.**

1. 설치한 `D:\patent_finder` 폴더를 통째로 복사해 `D:\patent_finder_fine` 으로 둔다 (`.venv` 포함. 새로 설치해도 된다).
2. `D:\patent_finder_fine\config\default.yaml` 을 메모장으로 열어 `unit: paragraph` 를 `unit: fine` 으로 바꾼다.
3. 복사해 온 `data`, `artifacts`, `outputs` 폴더가 있으면 지운다 (기본 단위의 기록이라 세분 모드에서는 쓰지 않는다).
4. 그 폴더의 `mark.bat`, `run.bat`, `train.bat` 을 평소처럼 쓴다. 결과와 판정은 그 폴더 안에 쌓인다.

- 같은 폴더에서 `unit` 만 바꾸면 그 뒤에 넣는 문서는 새 단위로 다시 나뉘고 seed 분류기가 새로 만들어진다. 이전 단위로 남긴 판정은 DB에 남지만 새 단위의 학습에는 쓰이지 않는다.
- 세분 모드의 판정 기준은 `docs/LABELING_GUIDE.md` 의 "판정 단위가 세분일 때" 절을 따른다.
- 합성 자료에서 잰 두 단위의 차이는 `README.md` 의 검수 결과에 있다. 세분 모드는 seed 분류기의 놓침이 늘었으므로, 판정을 쌓아 `train.bat` 으로 다시 학습한 뒤 쓰는 것을 전제로 한다.

## 4. 네트워크 차단 확인

1. `config/default.yaml` 의 `runtime.offline` 이 `true` 인지 확인한다. 이 값이 true이면 프로그램이 루프백 외 접속과 DNS 조회를 스스로 차단하고, 시도가 있으면 오류로 멈춘다.
2. PC의 네트워크를 끊거나 방화벽으로 막은 상태에서 `doctor --offline`, `analyze`, `review` 를 실행해 본다.
3. 모델 디렉터리 이름을 잠시 바꾼 뒤 `analyze` 를 실행해 "자동으로 내려받지 않습니다" 오류가 바로 나오는지 확인한다.

프로그램 안의 가드는 Python 소켓 수준이다. 최종 보장은 OS·방화벽 수준 차단이다.

## 5. 시험 실행 (선택)

GitHub Actions의 `Windows 시험` 워크플로가 3절의 설치와 아래 시험을 Windows 러너에서 실행한다. 릴리스 태그를 넣어 수동으로 실행하면 그 릴리스의 묶음을 받아 확인한다.

pytest와 reportlab을 함께 반입한 경우:

```powershell
.venv\Scripts\python -m pytest
```

모델이 없으면 실제 모델 시험 6개는 사유와 함께 건너뛴다.

pytest를 반입하지 않았으면 합성 보고자료를 분석해 정답표와 대조하는 것으로 확인한다:

```powershell
.venv\Scripts\python -m patent_marker.cli analyze --input tests\fixtures\divisions --output outputs\divisions-001
```

```powershell
.venv\Scripts\python tools\check_expected.py --results outputs\divisions-001\results.jsonl
```

macOS 개발 환경에서의 수치는 `README.md` 의 검수 결과에 있다. OS가 다르면 점수의 끝자리가 달라 경계에 있는 구간 몇 개는 판정이 바뀔 수 있다. 수치가 크게 다르면 설치와 모델 파일을 다시 확인한다.

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

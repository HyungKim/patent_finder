#!/usr/bin/env python3
"""사업부별 합성 보고자료 생성기 (테스트용).

    python tests/fixtures/build_division_reports.py      # tests/fixtures/divisions/ 에 생성

- 사업부 이름만 실제 조직명을 따랐고, 과제·수치·방법은 모두 시험을 위해 지어낸 내용이다.
  실제 사업 내용, 고객, 일정, 실적과 무관하다. 모든 장에 그 사실을 표시한다.
- 사업부마다 같은 내용을 PPTX(슬라이드)와 PDF(쪽) 두 형식으로 만든다. 30장 이상이다.
- 문단마다 작성 의도에 따른 정답(YES/NO/HOLD)을 division_expected_labels.csv 로 함께 낸다.
  정답 기준은 docs/LABELING_GUIDE.md 를 따른다.

PDF 생성에는 개발용 의존성 reportlab이 필요하다.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent / "divisions"
KEY_PATH = Path(__file__).resolve().parent / "division_expected_labels.csv"
NOTICE = "테스트용 합성 자료 - 실제 사업 내용과 무관"
FILE_SUFFIX = "_기술보고_합성"
MIN_PAGES = 30


# ====================================================================== 내용 기술용 도우미
def Y(text: str, level: int = 0) -> tuple[int, str, str]:
    return (level, text, "YES")


def N(text: str, level: int = 0) -> tuple[int, str, str]:
    return (level, text, "NO")


def H(text: str, level: int = 0) -> tuple[int, str, str]:
    return (level, text, "HOLD")


def bullets(title: str, *items: tuple[int, str, str], notes: str | None = None) -> dict[str, Any]:
    return {"type": "bullets", "title": title, "items": list(items), "notes": notes}


def two_col(title: str, left_head: str, left: list, right_head: str, right: list) -> dict[str, Any]:
    return {"type": "two_col", "title": title, "left_head": left_head, "left": left,
            "right_head": right_head, "right": right}


def table(title: str, headers: list[str], rows: list[tuple[list[str], str]], widths: list[float]) -> dict[str, Any]:
    return {"type": "table", "title": title, "headers": headers, "rows": rows, "widths": widths}


def diagram(title: str, boxes: list[str], description: str, label: str = "YES") -> dict[str, Any]:
    return {"type": "diagram", "title": title, "boxes": boxes, "description": description, "label": label}


def chart(title: str, chart_title: str, categories: list[str], values: list[float], *items) -> dict[str, Any]:
    return {"type": "chart", "title": title, "chart_title": chart_title, "categories": categories,
            "values": values, "items": list(items)}


def image(title: str) -> dict[str, Any]:
    return {"type": "image", "title": title}


AGENDA = bullets(
    "목차",
    N("1. 요약 및 주요 지표"), N("2. 시장 및 고객 동향"), N("3. 과제별 기술 진행 현황"),
    N("4. 품질 및 공정 이슈"), N("5. 지식재산 및 리스크"), N("6. 일정, 예산과 향후 계획"),
)
PROJECT_HEADERS = ["과제", "단계", "진행률", "비고"]
RISK_HEADERS = ["리스크", "영향", "대응 계획"]


def deck(name: str, summary, market, projects, schedule, topics, issues, ip, risks, budget, plan, hidden) -> dict[str, Any]:
    slides = [
        AGENDA,
        bullets("요약 및 주요 지표", *[N(text) for text in summary]),
        bullets("시장 및 고객 동향", *[N(text) for text in market]),
        table("과제 현황", PROJECT_HEADERS, [(row, "NO") for row in projects], [0.34, 0.24, 0.16, 0.26]),
        bullets("추진 일정", *[N(text) for text in schedule]),
        *topics,
        issues,
        bullets("지식재산 현황", *[N(text) for text in ip]),
        table("리스크 및 대응", RISK_HEADERS, [(row, "NO") for row in risks], [0.3, 0.3, 0.4]),
        bullets("예산 및 투자", *[N(text) for text in budget]),
        bullets("향후 계획", *[N(text) for text in plan]),
        image("참고: 평가 현장 사진"),
    ]
    return {"name": name, "title": f"{name} 3분기 기술개발 현황 보고",
            "subtitle": f"{NOTICE} (2026년 10월)", "slides": slides, "hidden": hidden}


# ====================================================================== 광학솔루션사업부
OPTICS = deck(
    "광학솔루션사업부",
    summary=[
        "3분기 핵심 과제 7건 중 5건이 계획대로 진행 중",
        "고배율 줌 모듈 시제품 2차 평가 완료, 3차 평가는 11월 예정",
        "차량용 카메라 모듈 신뢰성 시험 1,000시간 통과",
        "신규 검사 설비 2대 도입 품의 진행 중",
    ],
    market=[
        "스마트폰 카메라는 고화소와 고배율 중심으로 사양 경쟁이 이어지는 추세",
        "차량용 카메라는 자율주행 단계 상향에 따라 탑재 수량이 증가할 전망",
        "고객사는 모듈 두께 축소와 납기 단축을 동시에 요구",
        "3D 센싱은 확장현실 기기와 로봇 분야로 적용처 확대가 기대됨",
    ],
    projects=[
        ["손떨림 보정 온도 보상", "시제품 평가", "80%", "정상"],
        ["자동 초점 정착 시간 단축", "알고리즘 검증", "65%", "정상"],
        ["능동 정렬 공정", "설비 적용", "70%", "정상"],
        ["폴디드 줌 열 보상", "설계", "45%", "일정 주의"],
        ["거리 측정 위상 복원", "시뮬레이션", "55%", "정상"],
        ["차량용 렌즈 히터", "신뢰성 시험", "75%", "정상"],
        ["이물 검사", "현장 시험", "60%", "정상"],
    ],
    schedule=[
        "10월: 시제품 3차 제작 및 내부 평가",
        "11월: 고객 샘플 제출과 공정 검증",
        "12월: 양산 이관 심의와 연간 결과 보고",
        "주간 진도 회의는 매주 수요일 오전에 진행",
    ],
    topics=[
        bullets(
            "손떨림 보정 액추에이터: 온도 드리프트 보상",
            N("배경: 연속 촬영 시 코일 발열로 홀 센서 출력이 변하여 보정 중심이 이동"),
            Y("제안: 홀 센서 출력을 코일 온도 추정값으로 보상하는 2단계 보정"),
            Y("코일 온도는 구동 전류의 제곱 누적값과 방열 시정수로 추정한다", 1),
            Y("추정 온도 구간별 오프셋 테이블을 선형 보간하여 홀 센서 목표값에서 차감한다", 1),
            Y("촬영 대기 중 2초마다 기준 위치로 복귀시켜 오프셋 테이블을 자동으로 갱신한다"),
            N("적용 대상: 차세대 고배율 모듈 시제품"),
            notes="발표자 노트: 온도 추정 계수는 모듈 종류별로 따로 둔다.",
        ),
        diagram(
            "보상 제어 블록 구성",
            ["자이로 센서", "떨림 추정기", "온도 추정기", "홀 센서 보정", "코일 구동기"],
            "온도 추정기 출력이 5도 이상 변하면 홀 센서 보정 블록이 오프셋 테이블을 다시 읽고, "
            "변화율이 초당 1도를 넘으면 보간 간격을 절반으로 줄인다.",
        ),
        chart(
            "평가 결과: 온도 드리프트 보상", "촬영 시간별 보정 중심 이동량 (μm)",
            ["2분", "5분", "10분"], [1.2, 2.1, 3.0],
            N("연속 촬영 10분 조건에서 보정 중심 이동량이 기존 대비 62% 감소"),
            N("저온 조건은 4분기에 추가 평가 예정"),
        ),
        two_col(
            "자동 초점: 정착 시간 단축",
            "문제", [N("렌즈 무게 증가로 초점 정착 시간이 길어짐"), N("촬영 자세에 따라 초점 위치 편차가 발생")],
            "제안", [
                Y("가속도 센서로 촬영 자세를 판별하고 자세별 중력 보상 전류를 구동 지령에 더한다"),
                Y("정착 구간에서 목표 위치 오차의 부호가 바뀌는 시점에 제동 펄스를 인가하여 오버슈트를 줄인다"),
            ],
        ),
        table(
            "자동 초점 구동 조건 비교", ["구분", "구동 방식", "정착 판정", "비고"],
            [
                (["기존", "개루프 스텝 구동", "고정 대기 시간 12 ms", "변경 없음"], "NO"),
                (["제안 1", "홀 센서 폐루프 제어에 자세별 중력 보상 전류를 더함", "오차 2 μm 이내 3회 연속", "신규"], "YES"),
                (["제안 2", "제동 펄스 폭을 렌즈 무게 등급별 테이블에서 선택", "오차 부호 반전 시점 기준", "신규"], "YES"),
                (["평가", "11월 2주차 시제품 평가", "-", "-"], "NO"),
            ],
            [0.12, 0.44, 0.3, 0.14],
        ),
        bullets(
            "평가 결과 및 남은 과제: 자동 초점",
            N("정착 시간 평균이 9.8 ms에서 6.1 ms로 단축"),
            N("저조도 조건의 초점 탐색 실패율은 추가 분석 필요"),
            H("기존 알고리즘을 일부 개선하여 적용"),
        ),
        bullets(
            "렌즈와 센서의 능동 정렬 공정",
            N("배경: 고화소 센서에서 주변부 해상력 편차가 수율의 주요 손실 요인"),
            Y("제안: 5개 영역의 초점 위치별 해상력 곡선 정점으로 센서 기울기를 계산하고 6축 스테이지를 2회 반복 보정한다"),
            Y("1차는 0.5 μm 간격의 거친 탐색, 2차는 정점 주변 3 μm 범위만 0.1 μm 간격으로 탐색한다", 1),
            Y("자외선 가경화 직후 해상력을 다시 측정하여 수축으로 생긴 기울기 변화를 다음 모듈의 보정 목표에 반영한다", 1),
            N("설비 2호기에 우선 적용하여 검증 중"),
        ),
        table(
            "능동 정렬 공정 조건", ["단계", "조건", "판정 기준"],
            [
                (["거친 정렬", "5개 영역 해상력 정점의 위치 편차로 기울기를 산출", "편차 4 μm 이내"], "YES"),
                (["정밀 정렬", "정점 주변 3 μm 구간만 0.1 μm 간격으로 재탐색", "편차 1 μm 이내"], "YES"),
                (["가경화", "자외선을 20% 출력으로 1초 조사한 뒤 100% 출력으로 3초 조사", "수축량 0.8 μm 이하"], "YES"),
                (["본경화", "열경화 조건은 기존과 동일", "변경 없음"], "NO"),
            ],
            [0.16, 0.56, 0.28],
        ),
        chart(
            "평가 결과: 능동 정렬", "영역별 해상력 편차 (%)",
            ["중심", "중간", "주변"], [2.0, 3.5, 5.1],
            N("주변부 해상력 편차가 9.4%에서 5.1%로 감소"),
            N("택트 타임은 모듈당 1.2초 증가"),
        ),
        bullets(
            "폴디드 줌 모듈: 열 초점 보상",
            N("배경: 장초점 모듈은 온도 변화에 따른 초점 이동이 큼"),
            Y("제안: 렌즈 배럴 온도 센서 값과 줌 위치의 2차원 테이블로 초점 보상량을 산출한다"),
            Y("테이블은 생산 단계에서 3개 온도점만 측정하고 중간 값은 배럴 재질의 팽창 계수로 보간한다", 1),
            Y("프리즘 구동부는 볼 가이드의 예압을 자석 흡인력으로 유지하고 구동 코일을 프리즘 양측에 대칭으로 배치한 구조"),
            N("양산 적용 여부는 고객 일정에 따라 결정"),
        ),
        diagram(
            "프리즘 구동부 구조",
            ["프리즘 홀더", "볼 가이드", "구동 코일(좌)", "구동 코일(우)", "위치 센서"],
            "좌우 구동 코일의 전류 차이로 프리즘의 좌우 기울기를 보정하며, 위치 센서 두 개의 출력 차이가 "
            "기준을 넘으면 전류 차이를 출력 차이에 비례하도록 제어한다.",
        ),
        bullets(
            "평가 결과 및 남은 과제: 폴디드 줌",
            N("고온 60도 조건의 초점 이동량이 기존 대비 절반 수준"),
            N("낙하 충격 후 프리즘 위치 복원 성능은 추가 시험 예정"),
            N("고객 요구 사양 확정은 11월 말로 예상"),
        ),
        bullets(
            "거리 측정 센서: 다중 주파수 위상 복원",
            N("배경: 단일 변조 주파수는 측정 가능 거리와 정밀도가 서로 상충"),
            Y("제안: 100 MHz와 20 MHz 두 변조 주파수의 위상을 조합하여 거리 모호성을 해소한다"),
            Y("저주파 위상으로 구간 번호를 정하고 고주파 위상으로 구간 안의 거리를 계산한다", 1),
            Y("두 주파수의 진폭 비가 기준 범위를 벗어난 화소는 다중 경로 반사로 판정하여 주변 화소의 중앙값으로 대체한다", 1),
            Y("광원 구동 전류는 포화 화소 비율이 5%를 넘으면 한 단계 낮추고 1% 미만이면 한 단계 높인다"),
        ),
        table(
            "눈 안전 보호 조건", ["항목", "조건", "비고"],
            [
                (["광 출력 감시", "감시용 수광 소자의 전류가 상한을 0.5 ms 이상 넘으면 구동기를 차단", "신규"], "YES"),
                (["확산판 이탈 감지", "확산판 위 투명 전극의 저항이 기준을 벗어나면 발광을 금지", "신규"], "YES"),
                (["인증 일정", "12월 시험 기관 접수", "-"], "NO"),
            ],
            [0.22, 0.62, 0.16],
        ),
        bullets(
            "평가 결과 및 남은 과제: 거리 측정",
            N("측정 가능 거리 6 m에서 거리 오차 1% 이내"),
            N("강한 햇빛 조건의 잡음 증가는 추가 대책 필요"),
            H("위상 계산 방식은 기존 방식을 바탕으로 보완"),
        ),
        bullets(
            "차량용 카메라 모듈: 렌즈 히터와 오염 감지",
            N("배경: 겨울철 렌즈 결빙과 김서림으로 인식 성능이 저하"),
            Y("제안: 렌즈 히터를 영상 선명도 변화로 제어한다"),
            Y("영상의 고주파 성분 감소율로 김서림을 감지하면 히터 듀티를 80%로 올리고 회복되면 30%로 유지한다", 1),
            Y("배터리 전압이 낮으면 히터 최대 듀티를 60%로 제한한다", 1),
            Y("렌즈 오염은 연속 프레임에서 움직이지 않는 저대비 영역의 면적으로 판정하고 면적이 3%를 넘으면 세척 요청 신호를 보낸다"),
        ),
        diagram(
            "오염 감지 처리 흐름",
            ["프레임 버퍼", "정지 영역 검출", "대비 계산", "면적 판정", "세척 요청"],
            "정지 영역 검출은 30개 프레임 동안 화소값 변화가 기준보다 작은 영역을 찾고, 차량이 정차 중일 때는 "
            "판정을 멈추어 배경을 오염으로 잘못 보는 것을 막는다.",
        ),
        chart(
            "평가 결과: 차량용 카메라", "조건별 인식 회복 시간 (초)",
            ["김서림", "결빙", "빗방울"], [8, 21, 5],
            N("김서림 조건에서 인식 회복 시간이 평균 8초"),
            N("신뢰성 시험 1,000시간 통과"),
        ),
        bullets(
            "이물 검사: 조리개 2단계 비교",
            N("배경: 센서 표면 이물은 출하 후 불량의 주요 원인"),
            Y("제안: 균일 광원 영상을 조리개 2단계로 촬영하고 두 결과를 비교하여 이물이 놓인 면을 구분한다"),
            Y("조리개를 조이면 선명해지는 흑점은 센서 표면, 변화가 없는 흑점은 필터 표면 이물로 분류한다", 1),
            Y("후보 영역은 고배율 카메라가 다시 촬영하고 분류기가 긁힘, 이물, 얼룩으로 나눈다"),
            N("검사 시간 목표: 모듈당 2.5초 이내"),
        ),
        table(
            "이물 검사 단계", ["단계", "처리", "비고"],
            [
                (["1차", "조리개 2단계 영상의 흑점 선명도 차이로 이물 위치 면을 구분", "신규"], "YES"),
                (["2차", "후보 영역만 고배율로 재촬영한 뒤 세 종류로 분류", "신규"], "YES"),
                (["기록", "검사 결과는 생산 관리 시스템에 저장", "기존"], "NO"),
            ],
            [0.14, 0.66, 0.2],
        ),
    ],
    issues=two_col(
        "품질 및 공정 이슈",
        "이슈", [N("시제품 2차 로트에서 접착제 도포량 편차가 발생"), N("렌즈 공급사 납기가 2주 지연")],
        "대응", [
            Y("도포 노즐 압력을 도포 직전 중량 측정값으로 보정하고 3회 연속 범위를 벗어나면 노즐을 자동 세정한다"),
            N("공급사와 주간 점검 회의를 운영하고 대체 공급선을 검토"),
        ],
    ),
    ip=[
        "3분기 발명신고 4건 접수, 2건은 출원 진행 중",
        "출원 검토 회의는 11월 2주차에 진행",
        "경쟁사 공개 특허 모니터링 결과는 별도 보고",
        "시제품 외부 공개 전에 출원 여부를 확정하는 절차를 유지",
    ],
    risks=[
        ["핵심 부품 수급 지연", "일정 2주 지연 가능", "대체 공급선 확보"],
        ["평가 장비 부족", "시험 대기 발생", "장비 임차 검토"],
        ["고객 사양 변경", "설계 재검토 필요", "변경 관리 회의 운영"],
    ],
    budget=[
        "3분기 예산 집행률 68%, 연간 계획 대비 3%p 낮음",
        "4분기 설비 투자 2건은 품의 승인 대기",
        "외주 평가 비용은 내년 예산에 반영 예정",
    ],
    plan=[
        "4분기에 시제품 3차 평가와 양산 이관 심의를 진행",
        "내년 1분기 신규 과제 2건 착수 예정",
        "평가 인력 2명 충원 요청",
    ],
    hidden=bullets(
        "백업: 보류 과제 메모",
        Y("자이로 신호의 저주파 성분은 고역 통과 필터의 차단 주파수를 촬영 모드별로 바꾸어 제거한다"),
        N("해당 과제는 내년에 재검토"),
    ),
)

# ====================================================================== 패키지솔루션사업부
PACKAGE = deck(
    "패키지솔루션사업부",
    summary=[
        "3분기 핵심 과제 8건 중 6건이 계획대로 진행 중",
        "대면적 기판 시제품의 휨 평가 2차 완료",
        "유리 코어 기판은 시험 라인 구축 단계",
        "검사 자동화 설비 1대 추가 도입 검토",
    ],
    market=[
        "고성능 연산용 반도체 수요 증가로 대면적 패키지 기판 수요가 확대되는 추세",
        "고객사는 미세 회로와 낮은 휨을 동시에 요구",
        "유리 코어 기판은 업계 전반에서 시험 생산이 진행 중",
        "원자재 가격 변동이 원가에 영향을 주고 있음",
    ],
    projects=[
        ["대면적 기판 휨 제어", "시제품 평가", "75%", "정상"],
        ["미세 회로 노광 보정", "공정 적용", "70%", "정상"],
        ["레이저 비아 품질", "조건 최적화", "60%", "정상"],
        ["도금 두께 균일도", "설비 개조", "50%", "일정 주의"],
        ["유리 코어 관통 구멍", "시험 라인", "35%", "정상"],
        ["검사 판정 자동화", "현장 시험", "65%", "정상"],
        ["표면처리 두께 관리", "조건 검증", "55%", "정상"],
        ["공정 간 보정", "기획", "20%", "신규"],
    ],
    schedule=[
        "10월: 대면적 시제품 3차 제작",
        "11월: 고객 평가용 샘플 제출",
        "12월: 시험 라인 설비 반입과 연간 보고",
        "공정 회의는 매주 목요일 오후에 진행",
    ],
    topics=[
        bullets(
            "대면적 기판: 휨 제어",
            N("배경: 기판이 커질수록 리플로 공정에서 휨이 커져 실장 불량으로 이어짐"),
            Y("제안: 층별 구리 잔존율 차이를 5% 이내로 맞추는 더미 패턴 자동 배치"),
            Y("기판을 2 mm 격자로 나누고 상하층 잔존율 차이가 큰 격자부터 그물 모양 더미를 채운다", 1),
            Y("신호선 주변 150 μm 이내에는 더미를 배치하지 않는다", 1),
            Y("적층 프레스 냉각 구간의 강온 속도를 분당 2도에서 1도로 낮추고 유리전이온도 부근에서 10분간 유지한다"),
            N("평가는 리플로 3회 후 휨 측정으로 진행"),
            notes="발표자 노트: 더미 배치 규칙은 제품군별 설계 기준서에 반영 예정.",
        ),
        table(
            "휨 제어 조건 비교", ["구분", "조건", "비고"],
            [
                (["기존", "더미 패턴 수동 배치, 강온 속도 분당 2도", "변경 없음"], "NO"),
                (["제안 1", "격자별 잔존율 차이로 더미를 자동 배치하고 신호선 주변은 제외", "신규"], "YES"),
                (["제안 2", "유리전이온도 부근에서 10분 유지한 뒤 분당 1도로 강온", "신규"], "YES"),
                (["일정", "11월 고객 평가", "-"], "NO"),
            ],
            [0.14, 0.68, 0.18],
        ),
        chart(
            "평가 결과: 휨 제어", "리플로 후 휨 측정값 (μm)",
            ["기존", "제안 1", "제안 1+2"], [118, 86, 64],
            N("제안 1과 2를 함께 적용하면 휨이 기존 대비 46% 감소"),
            N("대면적 제품군 전체로 확대 평가 예정"),
        ),
        bullets(
            "미세 회로 형성: 구역별 노광 보정",
            N("배경: 패널 위치에 따라 회로 폭 편차가 커 미세 회로 수율이 낮음"),
            Y("제안: 패널을 16개 구역으로 나누고 구역별 도금 두께 측정값으로 다음 로트의 노광량을 보정한다"),
            Y("회로 폭이 목표보다 1 μm 넓으면 해당 구역 노광량을 3% 줄이는 비례 계수를 사용한다", 1),
            Y("시드층 에칭 시간은 구역별 회로 폭 평균의 최솟값을 기준으로 정한다", 1),
            Y("정렬 마크 4점의 신축량으로 패널 변형을 계산하여 구역별 노광 배율을 따로 보정한다"),
            N("적용 설비: 노광기 3호기"),
        ),
        diagram(
            "노광 보정 데이터 흐름",
            ["도금 두께 측정", "회로 폭 측정", "보정 계수 계산", "노광 조건 갱신", "에칭 시간 설정"],
            "회로 폭 측정값이 관리 한계를 2회 연속 벗어난 구역은 보정 계수 계산에 직전 5개 로트의 가중 평균을 쓰고, "
            "최신 로트의 가중치를 0.5로 둔다.",
        ),
        bullets(
            "평가 결과 및 남은 과제: 노광 보정",
            N("구역 간 회로 폭 편차가 1.8 μm에서 0.9 μm로 감소"),
            N("선폭 8 μm 이하 제품의 수율 개선 효과는 추가 확인 필요"),
            H("에칭 조건은 기존 방식을 일부 조정하여 사용"),
        ),
        two_col(
            "레이저 비아 가공 품질",
            "문제", [N("비아 바닥 지름이 작아 도금 후 접속 신뢰성이 낮음"), N("수지 잔사가 남으면 층간 박리로 이어짐")],
            "제안", [
                Y("첫 펄스는 높은 에너지로, 이후 펄스는 60% 에너지로 조사하여 비아 상하 지름 비를 0.8 이상으로 유지한다"),
                Y("가공 직후 비아 바닥의 반사광 세기가 기준보다 낮으면 잔사로 판정하고 해당 비아에만 1펄스를 추가로 조사한다"),
            ],
        ),
        table(
            "레이저 비아 가공 조건", ["항목", "조건", "판정 기준"],
            [
                (["펄스 에너지", "1펄스는 100%, 2~3펄스는 60%로 단계 조사", "상하 지름 비 0.8 이상"], "YES"),
                (["잔사 판정", "바닥 반사광 세기가 기준의 70% 미만이면 추가 조사", "추가 조사 비율 2% 이하"], "YES"),
                (["잔사 제거", "약품 조건은 기존과 동일", "변경 없음"], "NO"),
                (["평가", "신뢰성 시험 500회 진행 중", "-"], "NO"),
            ],
            [0.18, 0.54, 0.28],
        ),
        bullets(
            "평가 결과: 레이저 비아",
            N("비아 접속 불량률이 0.12%에서 0.05%로 감소"),
            N("가공 시간은 패널당 4% 증가"),
            N("다음 분기에 양산 설비 2대로 확대 적용 검토"),
        ),
        bullets(
            "전해 도금: 두께 균일도",
            N("배경: 패널 가장자리에 전류가 집중되어 도금 두께 편차가 큼"),
            Y("제안: 차폐판 개구 크기를 패널 위치별로 다르게 설계하여 가장자리 전류 밀도를 낮춘다"),
            Y("개구율은 중심부 100%, 가장자리 70%로 두고 그 사이는 선형으로 변화시킨다", 1),
            Y("펄스 역전류 도금을 적용하여 정방향 20 ms, 역방향 1 ms를 반복하고 역방향 전류 밀도는 정방향의 3배로 설정한다"),
            Y("패널 양면 노즐의 분사 방향을 30초마다 반대로 전환하여 비아 내부의 농도 차이를 줄인다"),
            N("설비 개조 일정은 11월 정기 보수 기간에 맞춤"),
        ),
        table(
            "도금 조건과 결과", ["조건", "설정", "두께 편차"],
            [
                (["기존", "직류 도금, 균일 차폐판", "12%"], "NO"),
                (["제안", "위치별 개구율 차폐판과 펄스 역전류 도금을 조합", "6%"], "YES"),
                (["추가 검토", "양극 분할 전류 제어", "평가 예정"], "HOLD"),
            ],
            [0.18, 0.6, 0.22],
        ),
        bullets(
            "유리 코어 기판: 관통 구멍 형성",
            N("배경: 유리 코어는 평탄도가 좋지만 가공 중 균열이 쉽게 발생"),
            Y("제안: 레이저로 유리 내부를 개질한 뒤 습식 에칭으로 관통 구멍을 형성한다"),
            Y("개질 깊이는 유리 두께의 110%로 설정하여 양면에서 진행한 에칭이 만나도록 한다", 1),
            Y("구멍 지름이 목표보다 작으면 에칭 시간을 늘려 보정하고 에칭액 농도는 고정한다", 1),
            Y("유리 가장자리는 절단 후 레이저 면취로 미세 균열을 제거하고 수지 테두리를 덧씌워 충격을 흡수하는 구조"),
            N("시험 라인 구축은 12월 완료 목표"),
        ),
        diagram(
            "유리 코어 공정 흐름",
            ["레이저 개질", "습식 에칭", "시드층 형성", "구리 충전", "평탄화"],
            "구리 충전 단계에서는 구멍 중앙부터 채워지도록 억제제 농도를 높이고, 충전 중 전류 밀도를 "
            "3단계로 올려 내부 공극을 막는다.",
        ),
        bullets(
            "현재 수준과 남은 과제: 유리 코어",
            N("관통 구멍 지름 편차 3 μm 이내 달성"),
            N("열충격 시험 중 균열 발생률은 추가 개선 필요"),
            N("협력 기관과 공동 평가를 진행 중"),
            H("충전 조건은 업계에서 쓰는 방식을 참고하여 정함"),
        ),
        bullets(
            "자동 광학 검사: 가성 불량 저감",
            N("배경: 가성 불량이 많아 재검 인력 부담이 큼"),
            Y("제안: 1차 규칙 기반 검출 결과를 2차 분류기가 진성과 가성으로 나누고 가성 확률이 0.9 이상이면 재검 대상에서 제외한다"),
            Y("분류기 입력은 불량 후보 주변 64×64 화소 영상과 설계 데이터의 같은 영역 패턴을 함께 쓴다", 1),
            Y("검사원이 뒤집은 판정은 주 1회 재학습 데이터에 추가한다", 1),
            Y("제외된 후보 중 5%를 무작위로 뽑아 검사원이 확인하고 놓친 진성 불량이 나오면 확률 기준을 0.95로 올린다"),
            N("재검 시간 30% 절감이 목표"),
        ),
        table(
            "검사 단계", ["단계", "처리", "비고"],
            [
                (["1차", "규칙 기반 전수 검출", "기존"], "NO"),
                (["2차", "후보 영상과 설계 패턴을 함께 넣어 진성과 가성으로 분류", "신규"], "YES"),
                (["표본 확인", "제외 후보의 5%를 검사원이 재확인", "신규"], "YES"),
                (["보고", "주간 품질 회의에서 결과 공유", "-"], "NO"),
            ],
            [0.16, 0.66, 0.18],
        ),
        bullets(
            "표면처리와 솔더 레지스트",
            N("배경: 미세 패드에서 도금 두께 부족과 언더컷이 접합 불량의 원인"),
            Y("팔라듐 도금 두께를 도금액 온도와 패드 면적 비율로 예측하고 예측값이 하한에 가까우면 침지 시간을 10초 단위로 늘린다"),
            Y("솔더 레지스트 현상 시 진행 방향 앞쪽 노즐의 분사 압력은 낮게, 뒤쪽 노즐은 높게 설정하여 개구부 언더컷을 줄인다"),
            N("두 조건 모두 양산 라인 1개에서 검증 중"),
        ),
        chart(
            "평가 결과: 표면처리", "패드 크기별 팔라듐 두께 편차 (%)",
            ["대형 패드", "중형 패드", "미세 패드"], [4, 6, 9],
            N("미세 패드의 두께 편차가 14%에서 9%로 감소"),
            N("언더컷 불량은 절반 수준으로 감소"),
        ),
        bullets(
            "공정 간 보정 기획",
            N("배경: 앞 공정의 편차가 뒷 공정에서 누적되어 최종 품질에 영향"),
            Y("제안: 적층 후 두께 측정값을 다음 공정인 레이저 가공의 초점 위치 보정값으로 전달한다"),
            Y("앞 공정 측정값이 관리 한계의 80%를 넘으면 뒷 공정 조건을 보수적인 설정으로 자동 전환하고 작업자에게 알린다"),
            N("설비 간 데이터 연결 방식은 설비 제작사와 협의 중"),
            N("내년 1분기 시범 적용 목표"),
        ),
    ],
    issues=two_col(
        "품질 및 공정 이슈",
        "이슈", [N("외층 도금 설비 1대에서 두께 편차가 재발"), N("원자재 입고 검사 대기 시간이 증가")],
        "대응", [
            Y("설비 가동 전 더미 패널 2장으로 전류 분포를 측정하고 편차가 기준을 넘으면 양극 위치를 자동으로 조정한다"),
            N("입고 검사 인력을 한시적으로 1명 추가 배치"),
        ],
    ),
    ip=[
        "3분기 발명신고 5건 접수, 3건은 출원 진행 중",
        "출원 검토 회의는 11월 3주차에 진행",
        "유리 코어 관련 선행 기술 조사를 외부 기관에 의뢰",
        "공정 조건 공개 범위는 영업 비밀 관리 기준에 따름",
    ],
    risks=[
        ["원자재 가격 상승", "원가 부담 증가", "장기 공급 계약 검토"],
        ["시험 라인 설비 납기 지연", "일정 1개월 지연 가능", "대체 설비 임차 검토"],
        ["고객 인증 일정 변경", "양산 시점 조정", "분기별 일정 재협의"],
    ],
    budget=[
        "3분기 예산 집행률 71%, 설비 투자 비중이 가장 큼",
        "시험 라인 구축 비용은 2개 분기에 나누어 집행",
        "4분기 평가 비용 일부는 내년으로 이월 예정",
    ],
    plan=[
        "4분기에 대면적 시제품 3차 평가를 진행",
        "유리 코어 시험 라인을 12월까지 구축",
        "검사 자동화 대상 라인을 내년 2개로 확대 검토",
    ],
    hidden=bullets(
        "백업: 보류 과제 메모",
        Y("드릴 가공 깊이는 층간 정합 측정값으로 패널마다 보정하고 보정량이 30 μm를 넘으면 가공을 멈춘다"),
        N("해당 항목은 내년 과제 후보로 보류"),
    ),
)

# ====================================================================== 모빌리티솔루션사업부
MOBILITY = deck(
    "모빌리티솔루션사업부",
    summary=[
        "3분기 핵심 과제 8건 중 6건이 계획대로 진행 중",
        "조명 모듈 신규 과제의 시제품 평가 완료",
        "무선 배터리 관리 시스템은 차량 탑재 시험 준비 단계",
        "통신 모듈 인증 시험은 11월 접수 예정",
    ],
    market=[
        "전기차와 소프트웨어 중심 차량 확대로 전장 부품 수요가 증가하는 추세",
        "완성차 고객은 부품 통합과 경량화를 요구",
        "차량 조명은 디자인 차별화 수단으로 활용이 늘고 있음",
        "차량 통신 규격 전환 일정이 지역별로 달라 대응이 필요",
    ],
    projects=[
        ["조명 모듈 열 제어", "시제품 평가", "80%", "정상"],
        ["디지털 키 위치 판정", "알고리즘 검증", "65%", "정상"],
        ["통신 모듈 열 관리", "설계 검증", "60%", "정상"],
        ["무선 배터리 관리", "탑재 시험 준비", "55%", "일정 주의"],
        ["모터 토크 리플 저감", "양산 검증", "85%", "정상"],
        ["전력 변환기 효율", "설계", "50%", "정상"],
        ["실내 레이더 탑승자 감지", "현장 시험", "60%", "정상"],
        ["충전 통신 제어기", "기획", "25%", "신규"],
    ],
    schedule=[
        "10월: 조명 모듈 고객 평가와 통신 모듈 설계 검증",
        "11월: 인증 시험 접수와 탑재 시험 차량 준비",
        "12월: 양산 이관 심의와 연간 결과 보고",
        "과제 점검 회의는 격주 화요일에 진행",
    ],
    topics=[
        bullets(
            "차량 조명 모듈: 열과 밝기 제어",
            N("배경: 면광원 조명은 구간별 밝기 균일도와 발열 관리가 과제"),
            Y("제안: 발광 소자 열별 전류를 기판 온도 센서 값에 따라 낮추는 제어"),
            Y("기판 온도 85도부터 1도 오를 때마다 전류를 2%씩 낮추고 105도에서 최소 전류로 고정한다", 1),
            Y("열별 순방향 전압 편차로 단선과 단락을 판별하여 해당 열만 차단한다", 1),
            Y("환영 점등 동작은 화소별 밝기 목표값을 20 ms 주기로 보간하고 인접 화소 간 밝기 차이를 15% 이내로 제한한다"),
            N("적용 대상: 차세대 후미등 과제"),
            notes="발표자 노트: 온도 기준값은 차종별 열 해석 결과로 조정한다.",
        ),
        diagram(
            "조명 제어 블록",
            ["온도 센서", "전류 지령 계산", "열별 구동기", "전압 감시", "고장 판정"],
            "전압 감시 블록이 한 열의 전압이 평균보다 1.5 V 이상 낮다고 판단하면 단락으로 보고 그 열의 구동기를 끄며, "
            "나머지 열의 전류 지령을 올려 전체 밝기를 유지한다.",
        ),
        chart(
            "평가 결과: 조명 모듈", "기판 온도별 광량 유지율 (%)",
            ["85도", "95도", "105도"], [100, 84, 66],
            N("고온 조건에서도 규격 하한 60% 이상을 유지"),
            N("환영 점등 동작의 끊김 현상은 고객 평가에서 지적되지 않음"),
        ),
        bullets(
            "디지털 키: 거리 측정과 위치 판정",
            N("배경: 금속 차체 반사로 거리 측정 오차가 커지는 경우가 있음"),
            Y("제안: 차량 앵커 4개의 거리 측정값 중 첫 경로 신호가 약한 측정을 제외하고 나머지로 위치를 계산한다"),
            Y("제외 기준은 첫 경로 신호 세기와 전체 수신 세기의 차이가 6 dB 이상인 경우로 둔다", 1),
            Y("실내와 실외 판정은 실내 앵커와 실외 앵커 거리 차이의 이동 평균으로 하고 경계에서는 0.3 m의 히스테리시스를 둔다", 1),
            Y("거리 측정 왕복 시간이 직전 측정 대비 불연속적으로 늘어나면 중계 공격 가능성으로 보고 문 열림을 보류한 뒤 재측정을 3회 요구한다"),
            N("실차 시험은 주차장과 개활지 두 환경에서 진행"),
        ),
        table(
            "위치 판정 조건", ["항목", "조건", "동작"],
            [
                (["측정 제외", "첫 경로와 전체 수신 세기의 차이가 6 dB 이상", "해당 앵커 값을 계산에서 제외"], "YES"),
                (["실내 판정", "거리 차이 이동 평균이 경계를 0.3 m 이상 넘음", "시동 허용 상태로 전환"], "YES"),
                (["중계 공격 의심", "왕복 시간이 직전 대비 불연속으로 증가", "문 열림 보류 후 3회 재측정"], "YES"),
                (["시험 일정", "11월 실차 시험", "결과는 12월 보고"], "NO"),
            ],
            [0.2, 0.46, 0.34],
        ),
        bullets(
            "평가 결과 및 남은 과제: 디지털 키",
            N("실내 판정 정확도 97.8%"),
            N("우천 조건의 측정 오차는 추가 시험 필요"),
            H("판정 알고리즘을 개선하여 오차를 줄임"),
        ),
        two_col(
            "차량 통신 모듈: 열 관리와 안테나 전환",
            "문제", [N("고온 환경에서 통신 모듈 온도가 한계에 근접"), N("차체 가림으로 한쪽 안테나 수신 품질이 급격히 떨어짐")],
            "제안", [
                Y("모듈 온도가 95도를 넘으면 송신 전력을 1 dB씩 단계적으로 낮추고 90도 이하로 3초 유지되면 한 단계씩 복귀한다"),
                Y("안테나 2개의 수신 품질을 100 ms마다 비교하여 차이가 3 dB 이상이면 송신 안테나를 전환한다"),
            ],
        ),
        table(
            "통신 모듈 동작 조건", ["구분", "조건", "비고"],
            [
                (["전력 저감", "95도 초과 시 1 dB씩 단계 저감하고 90도 이하 3초 유지 시 복귀", "신규"], "YES"),
                (["안테나 전환", "수신 품질 차이 3 dB 이상이 2회 연속이면 전환", "신규"], "YES"),
                (["방열 구조", "기존 방열판 유지", "변경 없음"], "NO"),
                (["인증", "11월 시험 접수", "-"], "NO"),
            ],
            [0.18, 0.64, 0.18],
        ),
        bullets(
            "무선 배터리 관리 시스템",
            N("배경: 배선 제거로 무게와 조립 공수를 줄일 수 있으나 통신 신뢰성이 과제"),
            Y("제안: 셀 감시 노드가 채널별 패킷 오류율을 측정하여 2%를 넘는 채널을 호핑 목록에서 제외한다"),
            Y("제외된 채널은 60초 뒤 탐색 패킷으로 다시 평가한다", 1),
            Y("모든 노드가 주 제어기의 동기 신호를 받은 뒤 고정 지연 후 동시에 셀 전압을 측정한다"),
            Y("셀 균형 제어는 전압 편차가 15 mV를 넘는 셀에만 방전 저항을 연결하고 셀 온도가 50도를 넘으면 중단한다"),
            N("탑재 시험 차량은 11월에 준비"),
        ),
        diagram(
            "무선 배터리 관리 시스템 구성",
            ["셀 감시 노드", "무선 송수신부", "주 제어기", "채널 품질 관리", "균형 제어"],
            "주 제어기는 노드별 응답 지연이 기준을 넘는 횟수를 세어 3회 연속이면 그 노드의 전송 주기를 절반으로 줄이고, "
            "회복되면 원래 주기로 되돌린다.",
        ),
        bullets(
            "평가 결과 및 남은 과제: 무선 배터리 관리",
            N("실험실 조건에서 패킷 손실률 0.01% 이하"),
            N("금속 케이스 내부 반사 환경은 추가 평가 필요"),
            N("보안 인증 요구 사항은 고객과 협의 중"),
        ),
        bullets(
            "구동 모터: 토크 리플 저감",
            N("배경: 저속 조향 시 토크 리플이 조향감 저하로 이어짐"),
            Y("제안: 회전자 각도별 보상 전류 테이블로 코깅 토크를 상쇄한다"),
            Y("보상 테이블은 생산 라인에서 저속 회전 시 전류 리플을 측정하여 모터마다 기록한다", 1),
            Y("모터 온도가 오르면 자석 감자에 맞춰 보상 전류를 온도 계수로 줄인다", 1),
            Y("위치 센서 오차는 두 센서 출력으로 그린 도형의 타원도를 실시간으로 계산하여 진폭과 위상 차이를 보정한다"),
            N("양산 검증은 2개 라인에서 진행 중"),
        ),
        table(
            "모터 보상 조건", ["항목", "방법", "갱신 시점"],
            [
                (["코깅 보상", "각도별 보상 전류 테이블을 지령 전류에 더함", "생산 시 1회 기록"], "YES"),
                (["온도 보정", "권선 온도 추정값으로 보상 전류를 축소", "주행 중 1초 주기"], "YES"),
                (["센서 보정", "두 출력의 진폭 비와 위상 차이를 계산하여 보정", "시동 후 상시"], "YES"),
                (["검사", "출하 검사 항목은 기존과 동일", "-"], "NO"),
            ],
            [0.18, 0.54, 0.28],
        ),
        chart(
            "평가 결과: 구동 모터", "속도별 토크 리플 (%)",
            ["저속", "중속", "고속"], [1.1, 0.8, 0.7],
            N("저속 구간 토크 리플이 2.4%에서 1.1%로 감소"),
            N("소음 평가는 12월에 진행 예정"),
        ),
        bullets(
            "전력 변환기: 경부하 효율",
            N("배경: 경부하 구간의 효율이 낮아 대기 전력 손실이 큼"),
            Y("제안: 부하 전류가 정격의 30% 미만이면 3개 상 중 1개 상만 동작시키고 60%를 넘으면 3개 상을 모두 동작시킨다"),
            Y("상 수를 바꾸기 직전에 듀티를 미리 보상하여 출력 전압 흔들림을 줄인다", 1),
            Y("스위칭 데드타임을 인덕터 전류 크기에 따른 3구간 테이블로 조정하여 영전압 스위칭 구간을 넓힌다"),
            N("효율 목표: 경부하 구간 92% 이상"),
        ),
        table(
            "전력 변환기 동작 구간", ["부하 구간", "동작", "비고"],
            [
                (["30% 미만", "1개 상만 동작하고 나머지 상은 정지", "신규"], "YES"),
                (["30~60%", "2개 상 동작, 구간 경계에 5%의 히스테리시스를 둠", "신규"], "YES"),
                (["60% 초과", "3개 상 모두 동작", "기존"], "NO"),
                (["평가", "효율 측정은 10월 완료", "-"], "NO"),
            ],
            [0.2, 0.6, 0.2],
        ),
        bullets(
            "실내 레이더: 탑승자 감지",
            N("배경: 뒷좌석 방치 방지 규제로 탑승자 감지 기능 요구가 증가"),
            Y("제안: 호흡에 의한 미세 변위의 위상 변화 주기를 0.1~0.5 Hz 대역에서 검출하여 탑승자 유무를 판정한다"),
            Y("차량 진동 성분은 가속도 센서 신호와의 상관으로 추정하여 차감한다", 1),
            Y("인접 레이더와의 간섭을 피하기 위해 신호 시작 시간을 프레임마다 의사 난수로 흔들고 간섭이 검출된 구간은 인접 구간의 평균으로 대체한다"),
            N("현장 시험은 차종 3개에서 진행"),
        ),
        diagram(
            "레이더 신호 처리 흐름",
            ["거리 구간 선택", "위상 추출", "진동 성분 제거", "호흡 대역 검출", "탑승자 판정"],
            "호흡 대역 검출 결과가 10초 동안 연속으로 기준을 넘으면 탑승자로 판정하고, 문이 잠긴 뒤 30초 안에 "
            "판정되면 경고 신호를 보낸다.",
        ),
        bullets(
            "평가 결과 및 남은 과제: 실내 레이더",
            N("어린이 인형 시험에서 감지율 98%"),
            N("담요로 덮은 조건은 감지 시간이 길어져 추가 개선 필요"),
            N("규제 시험 일정은 내년 1분기"),
        ),
        bullets(
            "충전 통신 제어기 기획",
            N("배경: 충전기 종류에 따라 통신 신호 감쇠가 달라 연결 실패가 발생"),
            Y("제안: 연결 초기에 수신 신호 세기를 측정하여 송신 이득을 3단계 중에서 선택하고 실패하면 한 단계 높여 재시도한다"),
            Y("통신이 끊기면 충전 전류를 즉시 차단하지 않고 5초 동안 직전 전류의 50%로 유지하며 재연결을 시도한다"),
            N("관련 규격 개정 동향을 조사 중"),
            N("내년 1분기 시제품 제작 목표"),
        ),
    ],
    issues=two_col(
        "품질 및 공정 이슈",
        "이슈", [N("조명 모듈 시제품에서 접착 부위 들뜸이 2건 발생"), N("시험 차량 준비가 1주 지연")],
        "대응", [
            Y("접착 전 표면에 플라스마 처리를 추가하고 처리 후 10분 안에 접착제를 도포하도록 공정 순서를 바꾼다"),
            N("시험 차량은 다른 과제와 공유하여 일정 영향을 최소화"),
        ],
    ),
    ip=[
        "3분기 발명신고 6건 접수, 2건은 출원 진행 중",
        "출원 검토 회의는 11월 1주차에 진행",
        "고객 공동 개발 과제의 권리 귀속은 계약 조건에 따라 협의",
        "핵심 과제는 고객 시연 전에 출원 여부를 확정",
    ],
    risks=[
        ["규격 개정 일정 변경", "인증 재시험 가능", "분기별 규격 동향 점검"],
        ["시험 차량 부족", "탑재 시험 지연", "차량 공유 일정 조정"],
        ["반도체 부품 수급", "시제품 제작 지연", "대체 부품 사전 검증"],
    ],
    budget=[
        "3분기 예산 집행률 66%, 시험 비용 집행이 4분기에 집중",
        "인증 시험 비용은 연간 계획 범위 안",
        "시험 차량 임차 비용은 추가 품의 예정",
    ],
    plan=[
        "4분기에 조명 모듈 양산 이관 심의를 진행",
        "무선 배터리 관리 시스템 탑재 시험을 12월에 시작",
        "신규 과제 1건을 내년 1분기에 착수",
    ],
    hidden=bullets(
        "백업: 보류 과제 메모",
        Y("주차 중에는 레이더 송신 주기를 1초에서 5초로 늘리고 움직임이 감지되면 즉시 1초 주기로 되돌린다"),
        N("해당 항목은 전력 소모 검토 후 재논의"),
    ),
)

DECKS = [OPTICS, PACKAGE, MOBILITY]


# ====================================================================== 공통: 그림
def _png_bytes(width: int = 1200, height: int = 620, seed: int = 0) -> bytes:
    """글자가 없는 현장 사진 대용 그림 (스캔 이미지 모사)."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (width, height), (228, 230, 226))
    draw = ImageDraw.Draw(canvas)
    for index in range(9):
        left = 60 + index * 125
        top = 90 + ((index * 37 + seed * 53) % 160)
        draw.rectangle([left, top, left + 95, height - 110], outline=(88, 92, 96), width=4)
        draw.ellipse([left + 20, top + 25, left + 75, top + 80], outline=(120, 124, 128), width=3)
    draw.line([40, height - 80, width - 40, height - 80], fill=(88, 92, 96), width=5)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


# ====================================================================== PPTX
def render_pptx(deck: dict[str, Any], path: Path, seed: int) -> int:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    left, width = Inches(0.6), Inches(12.13)
    ink, accent = RGBColor(0x1F, 0x29, 0x37), RGBColor(0x1D, 0x5B, 0xB8)

    def style(paragraph, size: float, bold: bool = False, color=None) -> None:
        for run in paragraph.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            if color is not None:
                run.font.color.rgb = color

    def set_title(slide, text: str) -> None:
        title = slide.shapes.title
        title.left, title.top, title.width, title.height = left, Inches(0.35), width, Inches(0.9)
        title.text_frame.text = text
        title.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
        style(title.text_frame.paragraphs[0], 28, bold=True, color=ink)

    def fill_frame(frame, items, size: float = 18) -> None:
        frame.word_wrap = True
        for index, (level, text, _label) in enumerate(items):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = text
            paragraph.level = level
            style(paragraph, size if level == 0 else size - 2, color=ink)

    def textbox(slide, x, y, w, h, text: str, size: float, bold: bool = False, color=None):
        box = slide.shapes.add_textbox(x, y, w, h)
        box.text_frame.word_wrap = True
        box.text_frame.text = text
        style(box.text_frame.paragraphs[0], size, bold=bold, color=color or ink)
        return box

    def add_bullets(spec) -> Any:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        set_title(slide, spec["title"])
        body = slide.placeholders[1]
        body.left, body.top, body.width, body.height = left, Inches(1.45), width, Inches(5.3)
        fill_frame(body.text_frame, spec["items"])
        if spec.get("notes"):
            slide.notes_slide.notes_text_frame.text = spec["notes"]
        return slide

    def add_two_col(spec) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        set_title(slide, spec["title"])
        column = Inches(5.8)
        for index, (head, items) in enumerate(((spec["left_head"], spec["left"]), (spec["right_head"], spec["right"]))):
            x = left + index * (column + Inches(0.53))
            textbox(slide, x, Inches(1.5), column, Inches(0.5), head, 20, bold=True, color=accent)
            box = slide.shapes.add_textbox(x, Inches(2.15), column, Inches(4.4))
            fill_frame(box.text_frame, items, size=17)

    def add_table(spec) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        set_title(slide, spec["title"])
        rows = [spec["headers"]] + [cells for cells, _label in spec["rows"]]
        shape = slide.shapes.add_table(len(rows), len(spec["headers"]), left, Inches(1.6), width,
                                       Inches(0.6) * len(rows))
        for index, ratio in enumerate(spec["widths"]):
            shape.table.columns[index].width = int(width * ratio)
        for r, cells in enumerate(rows):
            for c, value in enumerate(cells):
                cell = shape.table.cell(r, c)
                cell.text = value
                for paragraph in cell.text_frame.paragraphs:
                    style(paragraph, 14, bold=(r == 0))

    def add_diagram(spec) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        set_title(slide, spec["title"])
        count = len(spec["boxes"])
        gap, box_h = Inches(0.45), Inches(1.0)
        box_w = int((width - gap * (count - 1)) / count)
        group = slide.shapes.add_group_shape()
        for index, label in enumerate(spec["boxes"]):
            x = left + index * (box_w + gap)
            box = group.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(1.9), box_w, box_h)
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor(0xE8, 0xEE, 0xF8)
            box.line.color.rgb = accent
            box.text_frame.text = label
            box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            style(box.text_frame.paragraphs[0], 15, color=ink)
            if index < count - 1:
                arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, x + box_w + Inches(0.07), Inches(2.25),
                                               gap - Inches(0.14), Inches(0.3))
                arrow.fill.solid()
                arrow.fill.fore_color.rgb = accent
                arrow.line.fill.background()
        textbox(slide, left, Inches(3.5), width, Inches(2.4), spec["description"], 18)

    def add_chart(spec) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        set_title(slide, spec["title"])
        data = CategoryChartData()
        data.categories = spec["categories"]
        data.add_series("측정값", spec["values"])
        graphic = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, left, Inches(1.5), Inches(6.2), Inches(4.8), data)
        graphic.chart.has_legend = False
        graphic.chart.has_title = True
        graphic.chart.chart_title.text_frame.text = spec["chart_title"]
        box = slide.shapes.add_textbox(left + Inches(6.6), Inches(1.9), Inches(5.5), Inches(4.0))
        fill_frame(box.text_frame, spec["items"], size=17)

    def add_image(spec) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        set_title(slide, spec["title"])
        slide.shapes.add_picture(io.BytesIO(_png_bytes(seed=seed)), left, Inches(1.45), width, Inches(5.4))

    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.left, slide.shapes.title.top = left, Inches(2.3)
    slide.shapes.title.width, slide.shapes.title.height = width, Inches(1.5)
    slide.shapes.title.text_frame.text = deck["title"]
    style(slide.shapes.title.text_frame.paragraphs[0], 36, bold=True, color=ink)
    subtitle = slide.placeholders[1]
    subtitle.left, subtitle.top, subtitle.width, subtitle.height = left, Inches(4.0), width, Inches(1.0)
    subtitle.text_frame.text = deck["subtitle"]
    style(subtitle.text_frame.paragraphs[0], 18)

    # 모든 장에 보이도록 마스터에 합성 자료 표시를 넣는다 (슬라이드 본문에는 넣지 않는다).
    notice = textbox(slide, left, Inches(7.05), Inches(8), Inches(0.3), NOTICE, 10, color=RGBColor(0x6B, 0x72, 0x80))
    element = notice._element
    element.getparent().remove(element)
    element.xpath("./p:nvSpPr/p:cNvPr")[0].set("id", "9001")
    element.xpath("./p:nvSpPr/p:cNvPr")[0].set("name", "Synthetic Notice")
    prs.slide_master.shapes._spTree.append(element)

    builders = {"bullets": add_bullets, "two_col": add_two_col, "table": add_table, "diagram": add_diagram,
                "chart": add_chart, "image": add_image}
    for spec in deck["slides"]:
        builders[spec["type"]](spec)

    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.left, slide.shapes.title.top = left, Inches(2.6)
    slide.shapes.title.width, slide.shapes.title.height = width, Inches(1.3)
    slide.shapes.title.text_frame.text = "감사합니다"
    style(slide.shapes.title.text_frame.paragraphs[0], 36, bold=True, color=ink)
    closing = slide.placeholders[1]
    closing.left, closing.top, closing.width, closing.height = left, Inches(4.0), width, Inches(0.9)
    closing.text_frame.text = "질의 응답"
    style(closing.text_frame.paragraphs[0], 20)
    visible = len(prs.slides)

    hidden = add_bullets(deck["hidden"])
    hidden._element.set("show", "0")
    prs.save(str(path))
    return visible


# ====================================================================== PDF
def render_pdf(deck: dict[str, Any], path: Path, seed: int) -> int:
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    font = "HYGothic-Medium"
    pdfmetrics.registerFont(UnicodeCIDFont(font))
    page_w, page_h = 960.0, 540.0
    left, width = 48.0, 864.0
    c = canvas.Canvas(str(path), pagesize=(page_w, page_h), pageCompression=1)
    state = {"page": 0}

    def text(x: float, y: float, value: str, size: float, gray: float = 0.12) -> None:
        c.setFillGray(gray)
        c.setFont(font, size)
        c.drawString(x, y, value)

    def wrapped(x: float, y: float, value: str, size: float, max_width: float, leading: float = 1.5) -> float:
        for line in simpleSplit(value, font, size, max_width):
            text(x, y, line, size)
            y -= size * leading
        return y

    def page_frame(title: str | None) -> None:
        state["page"] += 1
        if title:
            text(left, page_h - 62, title, 24)
            c.setStrokeGray(0.75)
            c.setLineWidth(1)
            c.line(left, page_h - 78, left + width, page_h - 78)
        text(left, 16, NOTICE, 9, gray=0.45)
        c.setFillGray(0.45)
        c.setFont(font, 9)
        c.drawRightString(left + width, 16, f"- {state['page']} -")

    def draw_items(x: float, y: float, items, max_width: float, size: float = 18) -> float:
        for level, value, _label in items:
            item_size = size if level == 0 else size - 2
            indent = 26.0 * level
            text(x + indent, y, "•" if level == 0 else "-", item_size)
            y = wrapped(x + indent + 18, y, value, item_size, max_width - indent - 18)
            y -= 9
        return y

    def draw_bullets(spec) -> None:
        page_frame(spec["title"])
        draw_items(left, page_h - 124, spec["items"], width)

    def draw_two_col(spec) -> None:
        page_frame(spec["title"])
        column = 408.0
        for index, (head, items) in enumerate(((spec["left_head"], spec["left"]), (spec["right_head"], spec["right"]))):
            x = left + index * (column + 48)
            text(x, page_h - 122, head, 19, gray=0.25)
            draw_items(x, page_h - 160, items, column, size=16.5)

    def draw_table(spec) -> None:
        page_frame(spec["title"])
        rows = [spec["headers"]] + [cells for cells, _label in spec["rows"]]
        size, pad = 14.0, 9.0
        columns = [width * ratio for ratio in spec["widths"]]
        edges = [left]
        for column in columns:
            edges.append(edges[-1] + column)
        y = page_h - 104
        c.setStrokeGray(0.35)
        c.setLineWidth(0.8)
        boundaries = [y]
        for cells in rows:
            lines = [simpleSplit(value, font, size, columns[i] - 2 * pad) for i, value in enumerate(cells)]
            height = max(len(item) for item in lines) * size * 1.45 + 2 * pad
            for i, cell_lines in enumerate(lines):
                line_y = y - pad - size
                for line in cell_lines:
                    text(edges[i] + pad, line_y, line, size)
                    line_y -= size * 1.45
            y -= height
            boundaries.append(y)
        for edge in edges:
            c.line(edge, boundaries[0], edge, boundaries[-1])
        for boundary in boundaries:
            c.line(edges[0], boundary, edges[-1], boundary)

    def draw_diagram(spec) -> None:
        page_frame(spec["title"])
        count = len(spec["boxes"])
        gap, box_h = 30.0, 60.0
        box_w = (width - gap * (count - 1)) / count
        top = page_h - 130
        c.setStrokeGray(0.3)
        c.setLineWidth(1.2)
        for index, label in enumerate(spec["boxes"]):
            x = left + index * (box_w + gap)
            c.roundRect(x, top - box_h, box_w, box_h, 6, stroke=1, fill=0)
            lines = simpleSplit(label, font, 13, box_w - 12)
            line_y = top - box_h / 2 + (len(lines) - 1) * 9 - 4
            for line in lines:
                c.setFillGray(0.12)
                c.setFont(font, 13)
                c.drawCentredString(x + box_w / 2, line_y, line)
                line_y -= 18
            if index < count - 1:
                c.line(x + box_w + 5, top - box_h / 2, x + box_w + gap - 5, top - box_h / 2)
                c.line(x + box_w + gap - 11, top - box_h / 2 + 5, x + box_w + gap - 5, top - box_h / 2)
                c.line(x + box_w + gap - 11, top - box_h / 2 - 5, x + box_w + gap - 5, top - box_h / 2)
        wrapped(left, top - box_h - 60, spec["description"], 18, width)

    def draw_chart(spec) -> None:
        page_frame(spec["title"])
        text(left, page_h - 116, spec["chart_title"], 13, gray=0.25)
        base_y, chart_h, chart_w = 150.0, 220.0, 360.0
        c.setStrokeGray(0.3)
        c.setLineWidth(1)
        c.line(left + 30, base_y, left + 30 + chart_w, base_y)
        c.line(left + 30, base_y, left + 30, base_y + chart_h)
        peak = max(spec["values"])
        slot = chart_w / len(spec["values"])
        for index, (category, value) in enumerate(zip(spec["categories"], spec["values"])):
            bar_h = chart_h * 0.9 * value / peak
            x = left + 30 + slot * index + slot * 0.25
            c.setFillGray(0.62)
            c.rect(x, base_y, slot * 0.5, bar_h, stroke=0, fill=1)
            c.setFillGray(0.12)
            c.setFont(font, 11)
            c.drawCentredString(x + slot * 0.25, base_y + bar_h + 6, f"{value:g}")
            c.drawCentredString(x + slot * 0.25, base_y - 18, category)
        draw_items(left + 470, page_h - 160, spec["items"], width - 470, size=16.5)

    def draw_image(spec) -> None:
        page_frame(spec["title"])
        c.drawImage(ImageReader(io.BytesIO(_png_bytes(seed=seed))), left, 60, width=width, height=page_h - 160)

    page_frame(None)
    y = wrapped(left, page_h - 230, deck["title"], 32, width, leading=1.35)
    wrapped(left, y - 16, deck["subtitle"], 16, width)
    c.showPage()

    drawers = {"bullets": draw_bullets, "two_col": draw_two_col, "table": draw_table, "diagram": draw_diagram,
               "chart": draw_chart, "image": draw_image}
    for spec in deck["slides"]:
        drawers[spec["type"]](spec)
        c.showPage()

    page_frame(None)
    text(left, page_h - 250, "감사합니다", 34)
    text(left, page_h - 300, "질의 응답", 18, gray=0.3)
    c.showPage()
    c.save()
    return state["page"]


# ====================================================================== 정답표
def key_rows(deck: dict[str, Any]) -> list[dict[str, Any]]:
    """작성 의도에 따른 문단별 정답. slide는 PPTX 슬라이드 번호이자 PDF 쪽 번호다."""
    document = deck["name"] + FILE_SUFFIX
    rows: list[dict[str, Any]] = []

    def add(slide: int, kind: str, label: str, value: str, scope: str = "both") -> None:
        rows.append({"document": document, "slide": slide, "kind": kind, "label": label, "scope": scope, "text": value})

    add(1, "title", "NO", deck["title"])
    add(1, "subtitle", "NO", deck["subtitle"])
    for number, spec in enumerate(deck["slides"], start=2):
        add(number, "title", "NO", spec["title"])
        kind = spec["type"]
        if kind == "bullets":
            for _level, value, label in spec["items"]:
                add(number, "bullet", label, value)
            if spec.get("notes"):
                add(number, "notes", "NO", spec["notes"], scope="pptx_notes")
        elif kind == "two_col":
            add(number, "heading", "NO", spec["left_head"])
            add(number, "heading", "NO", spec["right_head"])
            for _level, value, label in spec["left"] + spec["right"]:
                add(number, "bullet", label, value)
        elif kind == "table":
            add(number, "table_header", "NO", " | ".join(spec["headers"]))
            for cells, label in spec["rows"]:
                add(number, "table_row", label, max(cells, key=len))
        elif kind == "diagram":
            for box in spec["boxes"]:
                add(number, "diagram_box", "NO", box)
            add(number, "description", spec["label"], spec["description"])
        elif kind == "chart":
            add(number, "chart_title", "NO", spec["chart_title"])
            for _level, value, label in spec["items"]:
                add(number, "bullet", label, value)
    closing = len(deck["slides"]) + 2
    add(closing, "title", "NO", "감사합니다")
    add(closing, "subtitle", "NO", "질의 응답")
    hidden = deck["hidden"]
    add(closing + 1, "title", "NO", hidden["title"], scope="pptx_hidden")
    for _level, value, label in hidden["items"]:
        add(closing + 1, "bullet", label, value, scope="pptx_hidden")
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for seed, deck_spec in enumerate(DECKS):
        base = OUT_DIR / (deck_spec["name"] + FILE_SUFFIX)
        slides = render_pptx(deck_spec, base.with_suffix(".pptx"), seed)
        pages = render_pdf(deck_spec, base.with_suffix(".pdf"), seed)
        if slides < MIN_PAGES or pages < MIN_PAGES or slides != pages:
            print(f"오류: {deck_spec['name']} 장수 확인 필요 (PPTX {slides}, PDF {pages})", file=sys.stderr)
            return 1
        deck_rows = key_rows(deck_spec)
        rows.extend(deck_rows)
        counts = {label: sum(row["label"] == label and row["scope"] == "both" for row in deck_rows)
                  for label in ("YES", "NO", "HOLD")}
        print(f"{deck_spec['name']}: PPTX {slides}장(+숨김 1장), PDF {pages}쪽 · 정답 YES {counts['YES']}, "
              f"NO {counts['NO']}, HOLD {counts['HOLD']}")
    # 정답표는 보고자료 폴더 밖에 둔다 (폴더째 분석할 때 미지원 파일로 잡히지 않게).
    with KEY_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["document", "slide", "kind", "label", "scope", "text"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"정답표: {KEY_PATH} ({len(rows)}행)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""합성 시험 자료의 정답표와 분석 결과(results.jsonl)를 대조한다.

    python tools/check_expected.py --results outputs/divisions-001/results.jsonl

Laya 비교 실험(docs/LAYA_EXPERIMENT.md)의 결과를 함께 주면, Laya 점수가 낮은 후보를 뺐을 때의 표를 덧붙인다.

    python tools/check_expected.py --results outputs/<run>/results.jsonl --laya outputs/<run>/laya/laya_results.jsonl

정답표는 자료를 만든 쪽의 작성 의도(YES/NO/HOLD)이며, 합성 자료에 대한 점검용이다.
여기서 나온 수치는 실제 사내 문서에서의 성능을 뜻하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_KEY = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "division_expected_labels.csv"


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def unit_of(row: dict) -> int | None:
    parts = row.get("source_locator", {}).get("parts") or [{}]
    locator = parts[0].get("locator", {})
    return locator.get("slide", locator.get("page"))


def home_segments(target: str, rows: list[dict]) -> list[dict]:
    """정답표 항목이 자리 잡은 구간: 같은 쪽에서 이 항목의 글자가 차지하는 비율이 가장 큰 구간.

    짧은 항목(블록 이름 등)의 낱말은 다른 긴 문장 안에도 나온다. 낱말이 들어 있다는 것만으로 그 문장까지
    이 항목의 구간으로 보면, 문장이 후보일 때 항목도 '잘못 표시'로 세게 된다. 그래서 가장 잘 맞는 구간만 쓴다.
    긴 항목이 여러 구간으로 나뉜 경우에는 그 조각 구간들이 모두 이 항목의 구간이다.
    """
    best, homes = 0.0, []
    for row in rows:
        text = squeeze(row["original_text"])
        if not text:
            continue
        if target in text:
            coverage = len(target) / len(text)
        elif len(text) >= 10 and text in target:
            coverage = 1.0
        else:
            continue
        if coverage > best + 1e-9:
            best, homes = coverage, [row]
        elif coverage >= best - 1e-9:
            homes.append(row)
    return homes


def compare(segments: dict[tuple[str, str, int | None], list[dict]], expected: list[dict],
            laya_scores: dict[str, float | None] | None = None) -> dict:
    """정답표 항목마다 TP/FN/FP/TN을 센다.

    - YES 항목: 자리 잡은 구간 중 하나라도 후보면 TP.
    - NO 항목: 자리 잡은 구간이 후보이고, 그 구간에 YES 항목이 함께 있지 않을 때만 FP.
      진짜 후보와 같은 구간에 묶여 함께 표시된 NO 항목은 잘못 표시로 세지 않고 shared에 따로 센다.
    """
    laya_scores = laya_scores or {}
    formats = sorted({key[1] for key in segments})
    documents = {key[0] for key in segments}
    placed: list[tuple[dict, str, list[dict]]] = []
    for item in expected:
        if item["label"] == "HOLD" or item["scope"] == "pptx_notes" or item["document"] not in documents:
            continue
        for fmt in formats:
            if item["scope"] == "pptx_hidden" and fmt != "pptx":
                continue
            rows = segments.get((item["document"], fmt, int(item["slide"])), [])
            placed.append((item, fmt, home_segments(squeeze(item["text"]), rows)))
    yes_homes = {row["segment_id"] for item, _fmt, homes in placed if item["label"] == "YES" for row in homes}

    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    laya_items: list[tuple[str, str, list[float | None]]] = []  # (형식, 정답, 이 항목을 후보로 만든 구간들의 Laya 점수)
    missed: list[tuple[str, str, dict, str]] = []
    wrong: list[tuple[str, str, dict, float]] = []
    for item, fmt, homes in placed:
        counter = stats[(item["document"], fmt)]
        candidates = [row for row in homes if row["final_decision"] == "CANDIDATE"]
        score = max((row["stage1_score"] or 0.0 for row in homes), default=0.0)
        if not homes:
            counter["unmatched"] += 1
        if item["label"] == "YES":
            counter["tp" if candidates else "fn"] += 1
            if not candidates:
                missed.append((item["document"], fmt, item, "추출 안 됨" if not homes else f"점수 {score:.2f}"))
        else:
            own = [row for row in candidates if row["segment_id"] not in yes_homes]
            if candidates and not own:
                counter["shared"] += 1
            candidates = own
            counter["fp" if candidates else "tn"] += 1
            if candidates:
                wrong.append((item["document"], fmt, item, score))
        laya_items.append((fmt, item["label"], [laya_scores.get(row["segment_id"]) for row in candidates]))
    return {"stats": stats, "laya_items": laya_items, "missed": missed, "wrong": wrong}


def laya_counts(laya_items: list[tuple[str, str, list[float | None]]], threshold: float) -> dict[str, dict[str, int]]:
    """Laya 점수가 threshold보다 낮은 후보 구간을 뺐을 때의 형식별 집계. 점수가 없는(판단 못 한) 구간은 빼지 않는다."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fmt, label, scores in laya_items:
        kept = any(score is None or score >= threshold for score in scores)
        counts[fmt][("tp" if kept else "fn") if label == "YES" else ("fp" if kept else "tn")] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True, help="analyze/export가 만든 results.jsonl")
    parser.add_argument("--expected", default=str(DEFAULT_KEY), help="정답표 CSV")
    parser.add_argument("--list", action="store_true", help="놓친 후보와 잘못 표시한 구간을 모두 출력")
    parser.add_argument("--laya", help="laya-compare가 만든 laya_results.jsonl. 주면 Laya가 '이견'을 낸 후보를 뺐을 때의 표도 낸다")
    parser.add_argument("--laya-agree-at", type=float, nargs="+", default=[0.1, 0.3, 0.5, 0.7],
                        help="Laya 표에 함께 보여 줄 동의 기준들 (기본: 0.1 0.3 0.5 0.7)")
    args = parser.parse_args(argv)

    segments: dict[tuple[str, str, int | None], list[dict]] = defaultdict(list)
    for line in Path(args.results).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        name = Path(row["file_name"])
        stem = name.stem[:-len(".marked")] if name.stem.endswith(".marked") else name.stem
        segments[(stem, name.suffix.lstrip(".").lower(), unit_of(row))].append(row)
    laya_scores: dict[str, float | None] = {}  # None은 Laya가 판단하지 못한 후보. 어떤 기준에서도 빼지 않는다
    laya_agree_at: float | None = None
    if args.laya:
        for line in Path(args.laya).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            laya_scores[row["segment_id"]] = row["laya_score"] if row["laya_status"] == "ok" else None
            laya_agree_at = row["agree_at"]
        candidates = {row["segment_id"] for rows in segments.values() for row in rows if row["final_decision"] == "CANDIDATE"}
        if not candidates & set(laya_scores):
            print("오류: --laya 파일에 이 results.jsonl의 후보가 하나도 없습니다. 같은 run의 파일인지 확인하세요.")
            return 2
    with Path(args.expected).open(encoding="utf-8-sig", newline="") as handle:
        expected = list(csv.DictReader(handle))
    result = compare(segments, expected, laya_scores)
    stats, laya_items, missed, wrong = result["stats"], result["laya_items"], result["missed"], result["wrong"]

    def ratio(numerator: int, denominator: int) -> str:
        return f"{numerator / denominator:.3f}" if denominator else "N/A"

    print("정답표 대조 (합성 자료, 작성 의도 기준. HOLD와 발표자 노트는 제외)")
    print(f"{'문서':24s} {'형식':5s} {'Recall':>7s} {'Precision':>9s} {'표시비율':>8s} {'TP':>4s} {'FN':>4s} {'FP':>4s} {'TN':>5s} {'미추출':>6s}")
    total: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (document, fmt), c in sorted(stats.items()):
        for key, value in c.items():
            total[fmt][key] += value
        items = c["tp"] + c["fn"] + c["fp"] + c["tn"]
        print(f"{document[:24]:24s} {fmt:5s} {ratio(c['tp'], c['tp'] + c['fn']):>7s} {ratio(c['tp'], c['tp'] + c['fp']):>9s} "
              f"{ratio(c['tp'] + c['fp'], items):>8s} {c['tp']:4d} {c['fn']:4d} {c['fp']:4d} {c['tn']:5d} {c['unmatched']:6d}")
    for fmt, c in sorted(total.items()):
        items = c["tp"] + c["fn"] + c["fp"] + c["tn"]
        print(f"{'전체':24s} {fmt:5s} {ratio(c['tp'], c['tp'] + c['fn']):>7s} {ratio(c['tp'], c['tp'] + c['fp']):>9s} "
              f"{ratio(c['tp'] + c['fp'], items):>8s} {c['tp']:4d} {c['fn']:4d} {c['fp']:4d} {c['tn']:5d} {c['unmatched']:6d}")

    shared = sum(c["shared"] for c in total.values())
    if shared:
        print(f"(진짜 후보와 같은 구간에 묶여 함께 표시된 NO 항목 {shared}건은 잘못 표시로 세지 않았다)")

    if args.laya:
        print("\nLaya 점수가 동의 기준보다 낮은 1차 후보를 뺐을 때 (실험용. 어느 기준도 검증되지 않았다)")
        print(f"{'동의 기준':24s} {'형식':5s} {'Recall':>7s} {'Precision':>9s} {'표시비율':>8s} {'TP':>4s} {'FN':>4s} {'FP':>4s} {'TN':>5s}")
        for threshold in [0.0, *sorted({*args.laya_agree_at, laya_agree_at})]:
            counts = laya_counts(laya_items, threshold)
            name = "1차만 (Laya 없이)" if threshold == 0.0 else f"{threshold:g}" + (" (이번 실행)" if threshold == laya_agree_at else "")
            for fmt, c in sorted(counts.items()):
                items = c["tp"] + c["fn"] + c["fp"] + c["tn"]
                print(f"{name:24s} {fmt:5s} {ratio(c['tp'], c['tp'] + c['fn']):>7s} {ratio(c['tp'], c['tp'] + c['fp']):>9s} "
                      f"{ratio(c['tp'] + c['fp'], items):>8s} {c['tp']:4d} {c['fn']:4d} {c['fp']:4d} {c['tn']:5d}")

    limit = None if args.list else 8
    print(f"\n놓친 후보 (FN) {len(missed)}건" + ("" if args.list or len(missed) <= 8 else " 중 8건"))
    for document, fmt, item, reason in missed[:limit]:
        print(f"  [{fmt}] {document[:10]} {item['slide']}장 · {reason} · {item['text'][:70]}")
    wrong.sort(key=lambda entry: -entry[3])
    print(f"\n후보로 잘못 표시한 구간 (FP) {len(wrong)}건" + ("" if args.list or len(wrong) <= 8 else " 중 점수 상위 8건"))
    for document, fmt, item, score in wrong[:limit]:
        print(f"  [{fmt}] {document[:10]} {item['slide']}장 · 점수 {score:.2f} · {item['kind']} · {item['text'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

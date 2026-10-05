#!/usr/bin/env python3
"""합성 시험 자료의 정답표와 분석 결과(results.jsonl)를 대조한다.

    python tools/check_expected.py --results outputs/divisions-001/results.jsonl

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


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True, help="analyze/export가 만든 results.jsonl")
    parser.add_argument("--expected", default=str(DEFAULT_KEY), help="정답표 CSV")
    parser.add_argument("--list", action="store_true", help="놓친 후보와 잘못 표시한 구간을 모두 출력")
    args = parser.parse_args(argv)

    segments: dict[tuple[str, str, int | None], list[dict]] = defaultdict(list)
    for line in Path(args.results).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        name = Path(row["file_name"])
        stem = name.stem[:-len(".marked")] if name.stem.endswith(".marked") else name.stem
        segments[(stem, name.suffix.lstrip(".").lower(), unit_of(row))].append(row)
    formats = sorted({key[1] for key in segments})
    documents = sorted({key[0] for key in segments})

    with Path(args.expected).open(encoding="utf-8-sig", newline="") as handle:
        expected = list(csv.DictReader(handle))

    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    missed: list[tuple[str, str, dict, str]] = []
    wrong: list[tuple[str, str, dict, float]] = []
    for item in expected:
        if item["label"] == "HOLD" or item["scope"] == "pptx_notes":
            continue
        for fmt in formats:
            if item["scope"] == "pptx_hidden" and fmt != "pptx":
                continue
            if item["document"] not in documents:
                continue
            target = squeeze(item["text"])
            found = [row for row in segments.get((item["document"], fmt, int(item["slide"])), [])
                     if target in squeeze(row["original_text"])
                     or (len(squeeze(row["original_text"])) >= 10 and squeeze(row["original_text"]) in target)]
            counter = stats[(item["document"], fmt)]
            flagged = any(row["final_decision"] == "CANDIDATE" for row in found)
            score = max((row["stage1_score"] or 0.0 for row in found), default=0.0)
            if not found:
                counter["unmatched"] += 1
            if item["label"] == "YES":
                counter["tp" if flagged else "fn"] += 1
                if not flagged:
                    missed.append((item["document"], fmt, item, "추출 안 됨" if not found else f"점수 {score:.2f}"))
            else:
                counter["fp" if flagged else "tn"] += 1
                if flagged:
                    wrong.append((item["document"], fmt, item, score))

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

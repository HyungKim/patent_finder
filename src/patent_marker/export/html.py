"""오프라인 HTML 보고서 (스펙 13절).

파일 하나로 완결되며 외부 자원(CDN, 글꼴, 추적)을 불러오지 않는다. 문서 텍스트는 모두 escape한다.
색과 함께 항상 텍스트 상태("후보", "비후보", "검토자 YES")를 표시한다.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from ..runtime import atomic_write_text, utc_now
from .data import target_text

_UNIT_LABEL = {"slide": "슬라이드", "page": "쪽", "block": "본문 요소", "line": "행"}

_BANNERS = {
    "SEED": ("seed", "임시 후보 표시 (SEED · 미검증)",
             "합성 예문으로 만든 seed 분류기의 결과입니다. 실제 문서에서의 Recall은 측정되지 않았습니다. "
             "놓친 후보가 있을 수 있으니 전체 내용을 함께 확인하세요."),
    "PILOT": ("pilot", "파일럿 모델",
              "평가 기준을 모두 충족하지 못한 모델입니다. 평가 보고서의 Recall과 신뢰구간을 확인하세요."),
    "PRODUCTION": ("production", "운영 모델", "검증된 threshold를 적용한 결과입니다."),
    None: ("untrained", "분류기 없음 (UNTRAINED)",
           "후보를 표시하지 않았습니다. 리뷰 화면에서 라벨을 수집한 뒤 분류기를 학습하세요."),
}

_CSS = """
:root{--bg:#f6f5f1;--surface:#fff;--ink:#1b1a18;--muted:#68655e;--line:#e2dfd7;--mark:#ffe7a3;--mark-line:#b97a00;
--accent:#1d5bb8;--warn-bg:#fff3dc;--warn-ink:#7d4300;--yes:#17693c;--no:#932626;--hold:#5b4a9c;--chip:#efede7}
@media (prefers-color-scheme:dark){:root{--bg:#151513;--surface:#1e1d1b;--ink:#eceae5;--muted:#a39f97;--line:#35332f;
--mark:#584200;--mark-line:#e3a72c;--accent:#8ab4f8;--warn-bg:#3a2a0c;--warn-ink:#ffd591;--yes:#6fd39b;--no:#f19a9a;
--hold:#b9aaf5;--chip:#2a2926}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;word-break:keep-all;overflow-wrap:anywhere}
main{max-width:980px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;line-height:1.25;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:19px;margin:44px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:26px 0 8px;color:var(--muted);font-weight:600}
.meta,.small{color:var(--muted);font-size:13px}
.mono{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-variant-numeric:tabular-nums}
.banner{margin:18px 0 10px;padding:12px 16px;border-radius:8px;border:1px solid var(--line);background:var(--surface)}
.banner strong{display:block;margin-bottom:2px}
.banner.seed,.banner.pilot,.banner.untrained{background:var(--warn-bg);color:var(--warn-ink);border-color:transparent}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:24px;line-height:1.2;font-variant-numeric:tabular-nums}
.card span{font-size:13px;color:var(--muted)}
.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;word-break:normal;overflow-wrap:break-word}
td.file{min-width:11em}td.where{min-width:8.5em}td.nowrap{white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
td.num{text-align:right;white-space:nowrap}
a{color:var(--accent)}
.controls{position:sticky;top:0;z-index:2;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);margin-top:28px;font-size:14px}
.doc{margin-top:8px}
.warn{margin:6px 0;padding:8px 12px;border-radius:6px;background:var(--warn-bg);color:var(--warn-ink);font-size:13px}
.para{display:grid;grid-template-columns:132px 1fr;gap:12px;padding:9px 12px;margin:4px 0;background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:6px}
.para.marked{border-left-color:var(--mark-line)}
.para .state{font-size:12px;line-height:1.5}
.para .text{white-space:pre-wrap}
.para.kind-title .text,.para.kind-heading .text{font-weight:600}
mark{background:var(--mark);color:inherit;padding:1px 0;border-radius:2px}
.chip{display:inline-block;padding:1px 7px;border-radius:10px;background:var(--chip);margin:0 4px 3px 0;white-space:nowrap}
.chip.cand{background:var(--mark);font-weight:600}
.chip.yes{color:var(--yes);border:1px solid currentColor;background:none}
.chip.no{color:var(--no);border:1px solid currentColor;background:none}
.chip.hold{color:var(--hold);border:1px solid currentColor;background:none}
body.only-marked .para:not(.marked){display:none}
@media (max-width:640px){.para{grid-template-columns:1fr}}
@media print{.controls{display:none}body{background:#fff}}
"""

_SCRIPT = """
document.getElementById('only').addEventListener('change',function(e){document.body.classList.toggle('only-marked',e.target.checked)});
document.querySelectorAll('time[datetime]').forEach(function(t){var d=new Date(t.getAttribute('datetime'));if(!isNaN(d))t.textContent=d.toLocaleString()});
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _score(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _highlight(text: str, spans: list[tuple[int, int]]) -> str:
    pieces = []
    cursor = 0
    for start, end in spans:
        pieces.append(_e(text[cursor:start]))
        pieces.append(f"<mark>{_e(text[start:end])}</mark>")
        cursor = end
    pieces.append(_e(text[cursor:]))
    return "".join(pieces)


def _coverage_line(document: dict[str, Any]) -> str:
    coverage = document["coverage"]
    label = _UNIT_LABEL.get(coverage.get("unit_type", ""), "단위")
    parts = [f"{label} {coverage.get('units_processed', 0)}/{coverage.get('units_total', 0)} 처리"]
    if coverage.get("units_failed"):
        parts.append(f"실패 {coverage['units_failed']} ({', '.join(map(str, coverage.get('failed_units', [])))})")
    if coverage.get("units_ocr_needed"):
        parts.append(f"OCR 필요 {coverage['units_ocr_needed']} ({', '.join(map(str, coverage.get('ocr_needed_units', [])))})")
    scope = coverage.get("scope") or {}
    names = {"speaker_notes": "발표자 노트", "headers_footers": "머리말·꼬리말", "footnotes": "각주",
             "hidden_slides": "숨긴 슬라이드"}
    scope_text = ", ".join(f"{names.get(key, key)} {'포함' if value else '제외'}" for key, value in scope.items())
    if scope_text:
        parts.append("분석 범위: " + scope_text)
    edits = coverage.get("edits") or {}
    if edits.get("dehyphenated"):
        parts.append(f"줄 끝 하이픈 병합 {edits['dehyphenated']}건")
    if edits.get("removed_headers_footers"):
        removed = ", ".join(f"“{item['text']}”" for item in edits["removed_headers_footers"])
        parts.append(f"반복 머리말·꼬리말 삭제: {removed}")
    return " · ".join(parts)


def _state_chips(segments: list[dict[str, Any]]) -> str:
    chips = []
    for segment in segments:
        decision = segment["final_decision"]
        if decision == "CANDIDATE":
            chips.append(f'<span class="chip cand">후보 <span class="mono">{_score(segment["stage1_score"])}</span></span>')
        elif decision == "NOT_CANDIDATE":
            chips.append(f'<span class="chip">비후보 <span class="mono">{_score(segment["stage1_score"])}</span></span>')
        else:
            chips.append('<span class="chip">미판정</span>')
        label = segment["human_label"]
        if label:
            suffix = " (일괄)" if segment["feedback"] and segment["feedback"]["implicit"] else ""
            chips.append(f'<span class="chip {label.lower()}">검토자 {label}{suffix}</span>')
    return "".join(chips)


def render_html(run: dict[str, Any]) -> str:
    stage = run["model_stage"] if run["classifier_version"] else None
    css_class, banner_title, banner_text = _BANNERS.get(stage, _BANNERS[None])
    policy = run.get("policy") or {}
    documents = run["documents"]
    analyzed = [d for d in documents if d["document_id"] and d["run_status"] in ("ok", "partial")]
    problems = [d for d in documents if d["run_status"] in ("unsupported", "failed")]
    partial = [d for d in analyzed if d["run_status"] == "partial"]
    segments = [s for d in analyzed for p in d["paragraphs"] for s in p["segments"]]
    marked = [(d, p, s) for d in analyzed for p in d["paragraphs"] for s in p["segments"] if s["marked"]]
    marked.sort(key=lambda item: -(item[2]["stage1_score"] or 0.0))

    out: list[str] = []
    out.append("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">")
    out.append("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">")
    out.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    out.append(f"<title>특허 검토 후보 보고서 · {_e(run['run_id'])}</title><style>{_CSS}</style></head><body><main>")
    out.append("<h1>특허 검토 후보 보고서</h1>")
    threshold = policy.get("threshold")
    out.append(
        f"<p class=\"meta\">run <span class=\"mono\">{_e(run['run_id'])}</span> · 분석 시작 "
        f"<time datetime=\"{_e(run['started_at'])}\">{_e(run['started_at'])}</time> · 모델 "
        f"<span class=\"mono\">{_e(run['classifier_version'] or '없음')}</span> · 정책 "
        f"<span class=\"mono\">{_e(run['policy_version'] or '없음')}</span>"
        f"{' (' + _e(policy.get('status')) + ', threshold ' + _score(threshold) + ')' if policy else ''}</p>"
    )
    out.append(f"<div class=\"banner {css_class}\"><strong>{_e(banner_title)}</strong>{_e(banner_text)}</div>")
    out.append(
        "<p class=\"small\">이 보고서의 표시는 <b>특허 검토 후보</b>입니다. 등록 가능성, 신규성·진보성, 침해 여부를 "
        "판정하지 않습니다. 숫자는 ‘후보 점수’이며 특허 등록 확률이 아닙니다.</p>"
    )
    out.append("<div class=\"cards\">")
    for value, label in (
        (len(analyzed), "분석한 문서"), (len(segments), "분석 구간"), (len(marked), "표시된 구간"),
        (len(problems), "미지원·실패 문서"), (len(partial), "일부만 처리된 문서"),
    ):
        out.append(f"<div class=\"card\"><b>{value}</b><span>{_e(label)}</span></div>")
    out.append("</div>")

    if problems or partial:
        out.append("<h2>처리하지 못했거나 일부만 처리한 문서</h2>")
        out.append("<p class=\"small\">아래 문서는 ‘후보 없음’이 아닙니다. 분석되지 않은 부분은 직접 확인해야 합니다.</p>")
        out.append("<div class=\"tablewrap\"><table><thead><tr><th>파일</th><th>상태</th><th>사유</th></tr></thead><tbody>")
        for document in problems:
            name = document["file_name"] or Path(document["input_path"]).name
            reason = document["run_error"] or document["error"] or ""
            out.append(f"<tr><td class=\"file\">{_e(name)}</td><td class=\"nowrap\">{_e(document['run_status'])}</td>"
                       f"<td>{_e(reason)}</td></tr>")
        for document in partial:
            reasons = "; ".join(w["message"] for w in document["warnings"]
                                if w["code"] in ("ocr_needed", "image_only_slide", "page_failed", "slide_failed",
                                                 "unmapped_glyphs", "rotated_text", "segmentation_error"))
            out.append(f"<tr><td class=\"file\">{_e(document['file_name'])}</td><td class=\"nowrap\">partial</td>"
                       f"<td>{_e(reasons)}</td></tr>")
        out.append("</tbody></table></div>")

    out.append("<h2>표시된 구간 위치 목록</h2>")
    if marked:
        out.append("<div class=\"tablewrap\"><table><thead><tr><th>#</th><th>문서</th><th>위치</th><th>후보 점수</th>"
                   "<th>대상 구간</th><th>사람 판정</th></tr></thead><tbody>")
        for index, (document, paragraph, segment) in enumerate(marked, start=1):
            snippet = target_text(paragraph, segment)
            snippet = snippet if len(snippet) <= 140 else snippet[:140] + "…"
            out.append(
                f"<tr><td class=\"num\">{index}</td><td class=\"file\">{_e(document['file_name'])}</td>"
                f"<td class=\"where\"><a href=\"#{_e(paragraph['paragraph_id'])}\">{_e(paragraph['location'])}</a></td>"
                f"<td class=\"num mono\">{_score(segment['stage1_score'])}</td><td>{_e(snippet)}</td>"
                f"<td>{_e(segment['human_label'] or '미검토')}</td></tr>"
            )
        out.append("</tbody></table></div>")
    else:
        out.append("<p class=\"small\">표시된 구간이 없습니다.</p>")

    out.append("<div class=\"controls\"><label><input type=\"checkbox\" id=\"only\"> 표시된 구간만 보기</label></div>")
    for document in analyzed:
        out.append(f"<section class=\"doc\"><h2>{_e(document['file_name'])}</h2>")
        out.append(f"<p class=\"small\">{_e(_coverage_line(document))} · 형식 {_e(document['format'])} · "
                   f"상태 {_e(document['parse_status'])}</p>")
        for warning in document["warnings"]:
            out.append(f"<div class=\"warn\">{_e(warning['message'])}</div>")
        current_group: tuple[Any, Any] | None = None
        for paragraph in document["paragraphs"]:
            group = (paragraph["unit"], paragraph["section_id"])
            if group != current_group:
                current_group = group
                unit_label = _UNIT_LABEL.get(document["coverage"].get("unit_type", ""), "")
                if paragraph["unit"] is not None:
                    heading = f"{unit_label} {paragraph['unit']}" if unit_label != "쪽" else f"{paragraph['unit']}쪽"
                else:
                    heading = "본문"
                if paragraph["section_title"]:
                    heading += f" · {paragraph['section_title']}"
                out.append(f"<h3>{_e(heading)}</h3>")
            classes = ["para", f"kind-{paragraph['kind']}"]
            if paragraph["marked_spans"]:
                classes.append("marked")
            chips = _state_chips(paragraph["segments"]) or '<span class="chip">구간 없음</span>'
            out.append(
                f"<div class=\"{' '.join(classes)}\" id=\"{_e(paragraph['paragraph_id'])}\">"
                f"<div class=\"state\">{chips}"
                f"<div class=\"small\">{_e(paragraph['location'])}</div></div>"
                f"<div class=\"text\">{_highlight(paragraph['original_text'], paragraph['marked_spans'])}</div></div>"
            )
        out.append("</section>")
    out.append(f"<p class=\"small\" style=\"margin-top:40px\">보고서 생성 <time datetime=\"{utc_now()}\">{utc_now()}</time>"
               " · 오프라인 특허 검토 후보 마킹 시스템</p>")
    out.append(f"</main><script>{_SCRIPT}</script></body></html>")
    return "".join(out)


def export_html(run: dict[str, Any], path: Path) -> dict[str, int]:
    atomic_write_text(path, render_html(run))
    marked = sum(1 for d in run["documents"] for p in d["paragraphs"] for s in p["segments"] if s["marked"])
    return {"marked": marked}

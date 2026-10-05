/* 로컬 검토 화면. 외부 자원을 쓰지 않으며 문서 텍스트는 textContent로만 넣는다. */
(function () {
  'use strict';

  const token = document.querySelector('meta[name="pm-token"]').content;
  const $ = (id) => document.getElementById(id);
  const FILTER_NAMES = {
    candidates: '후보만', all: '전체', unreviewed: '미검토', hold: '보류',
    noncandidate_sample: '비후보 표본', rule_suggest: '규칙 제안 (초기 라벨링)',
  };
  const REASON_NAMES = {
    CONCRETE_METHOD: '구체적 방법', GOAL_ONLY: '목표만', ADMIN_CONTENT: '행정 내용',
    INSUFFICIENT_CONTEXT: '문맥 부족', PARSING_ERROR: '파싱 오류', WRONG_SPAN: '구간 오류',
    DOMAIN_REVIEW_NEEDED: '전문 검토 필요', OTHER: '기타',
  };
  const FLAG_NAMES = {
    hidden_slide: '숨긴 슬라이드', speaker_notes: '발표자 노트', table_header: '표 머리행',
    title_inferred: '제목 추정', token_split: '토큰 분할', overlap: '중첩 구간', row_split: '행 분할',
    smartart: 'SmartArt', chart_title: '차트 제목', textbox: '텍스트 상자', segmentation_error: '분할 오류',
  };
  const UNIT_NAMES = { slide: '슬라이드', page: '쪽', block: '본문 요소', line: '행' };

  const state = {
    status: null, docs: [], docId: null, doc: null, group: 0, focus: 0,
    yes: new Set(), hold: new Set(), open: new Set(), tab: 'page',
    queue: { filter: 'candidates', offset: 0, limit: 30, items: [], total: 0, focus: 0 },
  };

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child);
    }
    return node;
  }

  async function api(path, body) {
    const options = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PM-Token': token },
      body: JSON.stringify(body),
    };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  let toastTimer = null;
  function toast(message) {
    const node = $('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  const score = (value) => (value === null || value === undefined ? '–' : value.toFixed(2));

  // ---------------------------------------------------------------- 상단 상태
  function renderStatus() {
    const status = state.status;
    const badge = $('model-badge');
    const model = status.model;
    badge.className = 'model';
    if (!model) {
      badge.classList.add('untrained');
      badge.textContent = '분류기 없음 (UNTRAINED) · 후보 표시 없음';
    } else {
      const names = { SEED: '임시 후보 (SEED · 미검증)', PILOT: '파일럿 모델', PRODUCTION: '운영 모델' };
      badge.classList.add((model.stage || '').toLowerCase());
      badge.textContent = `${names[model.stage] || model.stage} · ${model.model_version} · threshold ${score(model.threshold)} (${model.policy_status || '없음'})`;
    }
    const labels = status.counts.labels;
    const counts = $('counts');
    counts.replaceChildren(
      `검토자 ${status.reviewer_id} · 문서 `, el('b', { text: status.counts.documents }),
      ' · 구간 ', el('b', { text: status.counts.segments }),
      ' · YES ', el('b', { text: labels.YES || 0 }), ' · NO ', el('b', { text: labels.NO || 0 }),
      ' · HOLD ', el('b', { text: labels.HOLD || 0 }), ' · 미검토 ', el('b', { text: status.counts.unreviewed }),
    );
    const notice = $('notice');
    if (status.retraining.suggest) {
      notice.hidden = false;
      notice.textContent = `새 확정 라벨이 ${status.retraining.new_labels}개 쌓였습니다. 재학습을 제안합니다: snapshot → train → evaluate → promote (운영 모델은 자동으로 바뀌지 않습니다).`;
    } else {
      notice.hidden = true;
    }
  }

  // ---------------------------------------------------------------- 문서 목록
  function renderDocs() {
    const list = $('doc-list');
    list.replaceChildren();
    if (!state.docs.length) {
      list.append(el('li', { class: 'empty', text: '문서가 없습니다. ingest 또는 analyze 명령으로 문서를 넣으세요.' }));
      return;
    }
    for (const doc of state.docs) {
      const usable = doc.parse_status === 'ok' || doc.parse_status === 'partial';
      const meta = usable
        ? `${doc.format} · 구간 ${doc.segments} · 후보 ${doc.candidates} · 검토 ${doc.reviewed}/${doc.segments}` +
          (doc.parse_status === 'partial' ? ' · 일부만 처리' : '')
        : `${doc.parse_status} · ${doc.error || ''}`;
      list.append(el('li', {}, el('button', {
        'aria-current': doc.document_id === state.docId ? 'true' : null,
        onclick: () => selectDoc(doc.document_id),
      }, el('span', { class: 'name', text: doc.file_name }),
      el('span', { class: 'meta' + (usable && doc.parse_status === 'ok' ? '' : ' problem'), text: meta }))));
    }
  }

  async function selectDoc(documentId, keepGroup) {
    state.docId = documentId;
    state.doc = await api(`/api/document?id=${encodeURIComponent(documentId)}`);
    if (!keepGroup) {
      const pending = state.doc.groups.findIndex((group) => !group.reviewed);
      state.group = pending >= 0 ? pending : 0;
    }
    state.group = Math.min(state.group, Math.max(0, state.doc.groups.length - 1));
    loadGroupSelection();
    renderDocs();
    renderPage();
    if (state.tab === 'queue' && $('queue-doc-only').checked) loadQueue();
  }

  function loadGroupSelection() {
    state.yes = new Set();
    state.hold = new Set();
    state.focus = 0;
    const group = state.doc && state.doc.groups[state.group];
    if (!group) return;
    for (const segment of group.segments) {
      if (segment.human_label === 'YES') state.yes.add(segment.segment_id);
      if (segment.human_label === 'HOLD') state.hold.add(segment.segment_id);
    }
  }

  // ---------------------------------------------------------------- 공통 조각
  function textNode(segment) {
    const node = el('div', { class: `text kind-${segment.kind}` });
    for (const piece of segment.pieces) {
      node.append(piece.target ? document.createTextNode(piece.text) : el('span', { class: 'ctx', text: piece.text }));
    }
    return node;
  }

  function chipsNode(segment) {
    const chips = el('div', { class: 'chips' });
    if (segment.final_decision === 'CANDIDATE') {
      chips.append(el('span', { class: 'chip cand', text: `후보 ${score(segment.score)}` }));
    } else if (segment.final_decision === 'NOT_CANDIDATE') {
      chips.append(el('span', { class: 'chip', text: `비후보 ${score(segment.score)}` }));
    } else {
      chips.append(el('span', { class: 'chip', text: '미판정' }));
      if (segment.rule_hints.some((hint) => hint !== 'ADMIN_CUE')) {
        chips.append(el('span', { class: 'chip', text: '규칙 제안' }));
      }
    }
    if (segment.human_label) {
      chips.append(el('span', {
        class: `chip ${segment.human_label.toLowerCase()}`,
        text: `검토자 ${segment.human_label}${segment.label_implicit ? ' (일괄)' : ''}`,
      }));
    }
    for (const flag of segment.quality_flags) {
      chips.append(el('span', { class: 'chip flag', text: FLAG_NAMES[flag] || flag }));
    }
    if (segment.queue_reason) {
      chips.append(el('span', { class: 'chip', text: segment.queue_reason === 'rule' ? '규칙 순위' : '무작위 표본' }));
    }
    return chips;
  }

  function detailNode(segment, onLabeled) {
    const reasons = el('div', {});
    for (const code of state.status.reason_codes) {
      reasons.append(el('label', {}, el('input', { type: 'checkbox', value: code }), ' ' + (REASON_NAMES[code] || code)));
    }
    const memo = el('textarea', { placeholder: '메모 (선택)', 'aria-label': '메모' });
    const history = el('ul', { class: 'history' });
    const send = async (label) => {
      const codes = Array.from(reasons.querySelectorAll('input:checked')).map((input) => input.value);
      try {
        await api('/api/feedback', {
          target_type: 'segment', target_id: segment.segment_id, label,
          reason_codes: codes, comment: memo.value, prediction_id: segment.prediction_id,
        });
        toast(`${label}로 기록했습니다.`);
        await onLabeled(label);
      } catch (error) { toast(error.message); }
    };
    const actions = el('div', { class: 'actions' });
    for (const label of ['YES', 'NO', 'HOLD']) {
      actions.append(el('button', {
        class: label.toLowerCase(), 'aria-pressed': segment.human_label === label ? 'true' : 'false',
        onclick: () => send(label), text: label,
      }));
    }
    api(`/api/history?target_type=segment&target_id=${encodeURIComponent(segment.segment_id)}`).then((events) => {
      if (!events.length) history.append(el('li', { text: '판정 이력 없음' }));
      for (const event of events) {
        const when = new Date(event.created_at).toLocaleString();
        const codes = event.reason_codes.map((code) => REASON_NAMES[code] || code).join(', ');
        history.append(el('li', { text: `${when} · ${event.reviewer_id} · ${event.label} · ${event.source}${codes ? ' · ' + codes : ''}${event.comment ? ' · ' + event.comment : ''}` }));
      }
    }).catch(() => {});
    return el('div', { class: 'detail' },
      el('div', { class: 'muted', text: `${segment.location} · 모델 입력: ${segment.model_text}` }),
      el('div', { class: 'muted mono', text: `${segment.segment_id} · 모델 ${segment.classifier_version || '없음'} · threshold ${score(segment.threshold)}` }),
      reasons, memo, actions, history);
  }

  // ---------------------------------------------------------------- 페이지 검토
  function renderPage() {
    const head = $('doc-head');
    const strip = $('unit-strip');
    const panel = $('group');
    head.replaceChildren();
    strip.replaceChildren();
    panel.replaceChildren();
    if (!state.doc) {
      panel.append(el('p', { class: 'empty', text: '왼쪽에서 문서를 선택하세요.' }));
      return;
    }
    const doc = state.doc.document;
    const coverage = doc.coverage || {};
    const unit = UNIT_NAMES[coverage.unit_type] || '단위';
    head.append(el('h2', { text: doc.file_name }));
    head.append(el('div', {
      class: 'muted',
      text: `${doc.format} · 상태 ${doc.parse_status} · ${unit} ${coverage.units_processed ?? 0}/${coverage.units_total ?? 0} 처리` +
        (coverage.units_ocr_needed ? ` · OCR 필요 ${coverage.units_ocr_needed}` : ''),
    }));
    for (const warning of doc.warnings.slice(0, 6)) head.append(el('div', { class: 'warn', text: warning.message }));
    if (doc.error) head.append(el('div', { class: 'warn', text: doc.error }));
    for (const miss of state.doc.unparsed_positives) {
      head.append(el('div', { class: 'warn', text: `등록된 파싱 누락 후보${miss.unit ? ` (${miss.unit})` : ''}: ${miss.note}` }));
    }
    if (!state.doc.groups.length) {
      panel.append(el('p', { class: 'empty', text: '검토할 구간이 없습니다.' }));
      return;
    }
    state.doc.groups.forEach((group, index) => {
      strip.append(el('button', {
        role: 'tab', 'aria-selected': index === state.group ? 'true' : 'false',
        class: (group.candidates ? 'has-candidate ' : '') + (group.reviewed ? 'reviewed' : ''),
        title: `${group.label}${group.title ? ' · ' + group.title : ''} · 후보 ${group.candidates}${group.reviewed ? ' · 검토 완료' : ''}`,
        onclick: () => { state.group = index; loadGroupSelection(); renderPage(); },
        text: group.key.unit !== null ? String(group.key.unit) + (group.reviewed ? ' ✓' : '') : group.label + (group.reviewed ? ' ✓' : ''),
      }));
    });
    const group = state.doc.groups[state.group];
    panel.append(el('div', { class: 'group-head' },
      el('h3', { text: group.title && group.title !== group.label ? `${group.label} · ${group.title}` : group.label }),
      el('span', { class: 'muted', text: group.reviewed ? '검토 완료' : '미검토 구간 있음' }),
      el('span', { class: 'spacer' }),
      el('button', { onclick: () => registerMiss(group), text: '파싱 누락 후보 등록' })));

    group.segments.forEach((segment, index) => {
      const id = segment.segment_id;
      const checkbox = el('input', {
        type: 'checkbox', 'aria-label': '특허 검토 후보로 선택', checked: state.yes.has(id) ? true : null,
        onchange: (event) => { toggleYes(id, event.target.checked); },
      });
      const row = el('div', {
        class: 'row' + (segment.final_decision === 'CANDIDATE' ? ' candidate' : '') + (state.yes.has(id) ? ' checked' : ''),
        tabindex: '0', 'data-index': index,
        onfocus: () => { state.focus = index; },
      }, checkbox,
      el('div', {}, textNode(segment), chipsNode(segment)),
      el('div', { class: 'side' },
        el('button', { 'aria-pressed': state.hold.has(id) ? 'true' : 'false', onclick: () => toggleHold(id), text: '보류' }),
        el('button', { 'aria-expanded': state.open.has(id) ? 'true' : 'false', onclick: () => toggleOpen(id), text: '상세' })));
      if (state.open.has(id)) {
        row.append(detailNode(segment, async () => { await selectDoc(state.docId, true); await refreshStatus(); }));
      }
      panel.append(row);
    });

    const yes = state.yes.size;
    const hold = state.hold.size;
    const no = group.segments.length - yes - hold;
    const dropped = group.segments.filter((s) => s.final_decision === 'CANDIDATE' && !state.yes.has(s.segment_id) && !state.hold.has(s.segment_id)).length;
    panel.append(el('div', { class: 'complete' },
      el('span', { class: 'summary', text: `YES ${yes} · HOLD ${hold} · NO ${no}` + (dropped ? ` (시스템 후보 ${dropped}건이 NO로 기록됩니다)` : '') }),
      el('button', { onclick: () => moveGroup(-1), disabled: state.group === 0 ? true : null, text: '이전' }),
      el('button', { class: 'primary', onclick: completeGroup, text: '검토 완료: 선택 YES, 나머지 NO' }),
      el('button', { onclick: () => moveGroup(1), disabled: state.group >= state.doc.groups.length - 1 ? true : null, text: '다음' })));
    focusRow();
  }

  function focusRow() {
    const row = $('group').querySelector(`.row[data-index="${state.focus}"]`);
    if (row && state.tab === 'page' && !document.activeElement.closest('.detail')) row.focus({ preventScroll: false });
  }

  function toggleYes(id, value) {
    const on = value === undefined ? !state.yes.has(id) : value;
    if (on) { state.yes.add(id); state.hold.delete(id); } else { state.yes.delete(id); }
    renderPage();
  }

  function toggleHold(id) {
    if (state.hold.has(id)) state.hold.delete(id); else { state.hold.add(id); state.yes.delete(id); }
    renderPage();
  }

  function toggleOpen(id) {
    if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
    renderPage();
  }

  function moveGroup(delta) {
    const next = state.group + delta;
    if (next < 0 || next >= state.doc.groups.length) return;
    state.group = next;
    loadGroupSelection();
    renderPage();
  }

  async function completeGroup() {
    const group = state.doc.groups[state.group];
    const predictions = {};
    for (const segment of group.segments) if (segment.prediction_id) predictions[segment.segment_id] = segment.prediction_id;
    try {
      const result = await api('/api/page-review', {
        document_id: state.docId, unit: group.key.unit, section_id: group.key.section_id,
        yes_segment_ids: Array.from(state.yes), hold_segment_ids: Array.from(state.hold), predictions,
      });
      const recorded = result.recorded;
      toast(`${group.label} 검토 완료: YES ${recorded.YES}, HOLD ${recorded.HOLD}, NO ${recorded.NO}, 변경 없음 ${recorded.unchanged}`);
      const index = state.group;
      await selectDoc(state.docId, true);
      const pending = state.doc.groups.findIndex((item, position) => position > index && !item.reviewed);
      if (pending >= 0) { state.group = pending; loadGroupSelection(); renderPage(); }
      await refreshStatus();
      state.docs = await api('/api/documents');
      renderDocs();
    } catch (error) { toast(error.message); }
  }

  async function registerMiss(group) {
    const note = window.prompt(`${group.label}에서 파서가 읽지 못한 후보(이미지·도식 안의 내용 등)를 적어 주세요.`);
    if (!note || !note.trim()) return;
    try {
      await api('/api/unparsed', { document_id: state.docId, unit: group.key.unit, note });
      toast('파싱 누락 후보로 등록했습니다. 평가에서 놓친 후보(FN)로 계산됩니다.');
      await selectDoc(state.docId, true);
    } catch (error) { toast(error.message); }
  }

  // ---------------------------------------------------------------- 검토 큐
  async function loadQueue() {
    const queue = state.queue;
    const doc = $('queue-doc-only').checked && state.docId ? `&doc=${encodeURIComponent(state.docId)}` : '';
    const page = await api(`/api/queue?filter=${queue.filter}&offset=${queue.offset}&limit=${queue.limit}${doc}`);
    queue.items = page.items;
    queue.total = page.total;
    queue.focus = 0;
    renderQueue();
  }

  function renderQueue() {
    const queue = state.queue;
    const list = $('queue-list');
    list.replaceChildren();
    $('queue-total').textContent = queue.total
      ? `${queue.offset + 1}–${Math.min(queue.offset + queue.items.length, queue.total)} / ${queue.total}건`
      : '0건';
    $('queue-prev').disabled = queue.offset === 0;
    $('queue-next').disabled = queue.offset + queue.limit >= queue.total;
    if (!queue.items.length) {
      list.append(el('p', { class: 'empty', text: '이 보기에 해당하는 구간이 없습니다.' }));
      return;
    }
    queue.items.forEach((segment, index) => {
      const label = async (value) => {
        try {
          await api('/api/feedback', { target_type: 'segment', target_id: segment.segment_id, label: value, prediction_id: segment.prediction_id });
          segment.human_label = value;
          segment.label_implicit = false;
          toast(`${value}로 기록했습니다.`);
          renderQueue();
          await refreshStatus();
        } catch (error) { toast(error.message); }
      };
      const actions = el('div', { class: 'actions' });
      for (const value of ['YES', 'NO', 'HOLD']) {
        actions.append(el('button', {
          class: value.toLowerCase(), 'aria-pressed': segment.human_label === value ? 'true' : 'false',
          onclick: () => label(value), text: `${value} (${value[0]})`,
        }));
      }
      actions.append(el('button', { onclick: () => { toggleOpenQueue(segment.segment_id); }, text: '사유·메모·이력' }));
      const card = el('div', {
        class: 'card' + (segment.final_decision === 'CANDIDATE' ? ' candidate' : ''), tabindex: '0', 'data-index': index,
        onfocus: () => { queue.focus = index; },
      }, el('div', { class: 'where', text: `${segment.file_name} · ${segment.location}${segment.section_title ? ' · ' + segment.section_title : ''}` }),
      textNode(segment), chipsNode(segment), actions);
      card._label = label;
      if (state.open.has(segment.segment_id)) {
        card.append(detailNode(segment, async (value) => { segment.human_label = value; segment.label_implicit = false; renderQueue(); await refreshStatus(); }));
      }
      list.append(card);
    });
  }

  function toggleOpenQueue(id) {
    if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
    renderQueue();
  }

  // ---------------------------------------------------------------- 탭·키보드
  function setTab(tab) {
    state.tab = tab;
    $('tab-page').setAttribute('aria-selected', tab === 'page' ? 'true' : 'false');
    $('tab-queue').setAttribute('aria-selected', tab === 'queue' ? 'true' : 'false');
    $('view-page').hidden = tab !== 'page';
    $('view-queue').hidden = tab !== 'queue';
    const keys = $('keys');
    keys.replaceChildren();
    const hints = tab === 'page'
      ? [['↑ ↓', '이동'], ['Space', '후보 선택'], ['H', '보류'], ['Enter', '검토 완료'], ['[ ]', '이전·다음']]
      : [['J K', '이동'], ['Y N H', '판정']];
    for (const [key, text] of hints) keys.append(el('kbd', { text: key }), ` ${text}  `);
    if (tab === 'queue') loadQueue(); else renderPage();
  }

  document.addEventListener('keydown', (event) => {
    const tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'select' || (tag === 'input' && event.target.type !== 'checkbox')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key;
    if (state.tab === 'page' && state.doc && state.doc.groups.length) {
      const group = state.doc.groups[state.group];
      const segment = group.segments[state.focus];
      if (key === 'ArrowDown' || key === 'j') { state.focus = Math.min(state.focus + 1, group.segments.length - 1); focusRowForce(); event.preventDefault(); }
      else if (key === 'ArrowUp' || key === 'k') { state.focus = Math.max(state.focus - 1, 0); focusRowForce(); event.preventDefault(); }
      else if ((key === ' ' || key === 'y') && segment && tag !== 'button' && tag !== 'input') { toggleYes(segment.segment_id); event.preventDefault(); }
      else if (key === 'h' && segment) { toggleHold(segment.segment_id); event.preventDefault(); }
      else if (key === 'Enter' && tag !== 'button') { completeGroup(); event.preventDefault(); }
      else if (key === '[') moveGroup(-1);
      else if (key === ']') moveGroup(1);
    } else if (state.tab === 'queue' && state.queue.items.length) {
      const queue = state.queue;
      const cards = $('queue-list').querySelectorAll('.card');
      if (key === 'j' || key === 'ArrowDown') { queue.focus = Math.min(queue.focus + 1, cards.length - 1); cards[queue.focus].focus(); event.preventDefault(); }
      else if (key === 'k' || key === 'ArrowUp') { queue.focus = Math.max(queue.focus - 1, 0); cards[queue.focus].focus(); event.preventDefault(); }
      else if (['y', 'n', 'h'].includes(key) && tag !== 'button') {
        const card = cards[queue.focus];
        if (card && card._label) card._label({ y: 'YES', n: 'NO', h: 'HOLD' }[key]);
      }
    }
  });

  function focusRowForce() {
    const row = $('group').querySelector(`.row[data-index="${state.focus}"]`);
    if (row) row.focus();
  }

  async function refreshStatus() {
    state.status = await api('/api/status');
    renderStatus();
  }

  async function init() {
    await refreshStatus();
    const select = $('queue-filter');
    for (const name of state.status.filters) select.append(el('option', { value: name, text: FILTER_NAMES[name] || name }));
    select.addEventListener('change', () => { state.queue.filter = select.value; state.queue.offset = 0; loadQueue(); });
    $('queue-doc-only').addEventListener('change', () => { state.queue.offset = 0; loadQueue(); });
    $('queue-prev').addEventListener('click', () => { state.queue.offset = Math.max(0, state.queue.offset - state.queue.limit); loadQueue(); });
    $('queue-next').addEventListener('click', () => { state.queue.offset += state.queue.limit; loadQueue(); });
    $('tab-page').addEventListener('click', () => setTab('page'));
    $('tab-queue').addEventListener('click', () => setTab('queue'));
    state.docs = await api('/api/documents');
    renderDocs();
    setTab('page');
    const first = state.docs.find((doc) => doc.segments > 0);
    if (first) await selectDoc(first.document_id);
  }

  init().catch((error) => toast(error.message));
})();

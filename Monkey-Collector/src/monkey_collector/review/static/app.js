/* monkey · review — 단일 페이지. 상태는 해시에, 판정은 서버에.
   좌표 변환은 여기서 하지 않는다: 서버가 device px 와 그 프레임을 함께 주고,
   화면은 그걸 % 로만 바꾼다. 썸네일이든 원본이든 blink 든 같은 수식 하나다. */

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const S = {
  meta: null,
  apps: [],
  pkg: null,
  rows: [],
  counts: {},
  summary: {},
  scope: 'export',
  status: '',
  collapse: true,
  step: null,
  detail: null,
  xmlMode: 'html',   // html | raw
  diff: true,
  blink: false,
  blinkFlip: false,
  pendingReason: false,
  limit: 400,
};

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({ error: 'bad response' }));
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}
const post = (path, body) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body) });

let toastTimer = null;
function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2200);
}

/* ----------------------------------------------------------------- shots */

function markLayer(mark, size, big) {
  /* device px -> %, once. 썸네일과 원본이 같은 값을 쓴다. */
  const [w, h] = size || [0, 0];
  if (!mark || !w || !h || !mark.points || !mark.points.length) return null;
  if (mark.kind === 'point') {
    const [x, y] = mark.points[0];
    const node = el('div', `mark ${mark.emphasis === 'soft' ? 'soft' : ''} ${big ? 'big' : ''}`);
    node.style.left = `${(x / w) * 100}%`;
    node.style.top = `${(y / h) * 100}%`;
    node.appendChild(el('div', 'dot'));
    return node;
  }
  if (mark.kind === 'arrow') {
    const [[x1, y1], [x2, y2]] = mark.points;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'swipe-line');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.innerHTML =
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" vector-effect="non-scaling-stroke"/>` +
      `<circle cx="${x2}" cy="${y2}" r="${Math.max(w, h) / 90}"/>`;
    return svg;
  }
  return null;
}

function shot(pkg, index, opts = {}) {
  const box = el('div', opts.big ? 'big-shot' : 'shot');
  if (index == null || index < 0 || opts.missing) {
    box.appendChild(el('div', 'missing', '스크린샷 없음'));
    return box;
  }
  const img = el('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = `observation ${index}`;
  img.src = opts.big ? `/img/${pkg}/${index}` : `/img/${pkg}/${index}?w=240`;
  img.addEventListener('error', () => {
    box.innerHTML = '';
    box.appendChild(el('div', 'missing', '스크린샷 없음'));
  });
  box.appendChild(img);
  if (opts.tag) box.appendChild(el('div', 'tag', opts.tag));
  const layer = markLayer(opts.mark, opts.size, opts.big);
  if (layer) box.appendChild(layer);
  return box;
}

/* ------------------------------------------------------------------ apps */

function meterOf(app) {
  const total = Math.max(1, app.exportable);
  const wrap = el('div', 'meter');
  const keep = el('i', 'm-keep');
  keep.style.width = `${(app.kept / total) * 100}%`;
  const excl = el('i', 'm-excl');
  excl.style.width = `${(app.excluded / total) * 100}%`;
  wrap.append(keep, excl);
  return wrap;
}

function appCard(app) {
  const card = el('a', 'app-card');
  card.href = `#/app/${encodeURIComponent(app.package)}`;
  card.appendChild(el('div', 'pkg', app.package));

  const num = el('div', 'num');
  num.append(String(app.exportable), Object.assign(el('small'), { textContent: 'export 대상' }));
  card.appendChild(num);

  const row = el('div', 'row');
  if (app.live) {
    const live = el('span', 'badge live');
    live.append(el('i'), '수집 중');
    row.appendChild(live);
  }
  const ratio = app.exportable ? app.redundant / app.exportable : 0;
  if (ratio >= 0.3) {
    row.appendChild(el('span', 'badge warn',
      `서로 다른 화면 ${app.distinct}개뿐 · 중복 ${Math.round(ratio * 100)}%`));
  } else if (app.redundant) {
    row.appendChild(el('span', 'badge flat', `중복 ${app.redundant}`));
  }
  if (app.landscape_obs) row.appendChild(el('span', 'badge flat', `가로 ${app.landscape_obs}장`));
  if (app.stop_reason) row.appendChild(el('span', 'badge flat', app.stop_reason));
  card.appendChild(row);

  card.appendChild(meterOf(app));
  const legend = el('div', 'meter-legend');
  legend.innerHTML =
    `<span><b>${app.reviewed}</b> 검토</span>` +
    `<span><b>${app.excluded}</b> 제외</span>` +
    `<span>${app.triples} 수집</span>`;
  card.appendChild(legend);
  return card;
}

async function viewApps() {
  crumbs([]);
  $('#rules-open').hidden = true;
  const view = $('#view');
  view.className = 'view view-in';
  view.innerHTML = '';
  view.appendChild(Object.assign(el('h1', 'display'), { textContent: '무엇을 남길지 고른다' }));
  const lede = el('p', 'lede');
  lede.innerHTML =
    '수집된 triple 을 사람이 보고 제외한다. 여기서의 제외는 <b>export 에서 빠지는 것</b>일 뿐, ' +
    '수집된 바이트는 지우지 않는다. <code>raw/</code> 는 읽기만 하므로 다른 세션의 수집은 영향받지 않는다.';
  view.appendChild(lede);
  const grid = el('div', 'bento');
  view.appendChild(grid);
  for (let i = 0; i < 6; i += 1) {
    const sk = el('div', 'skeleton');
    sk.style.height = '212px';
    grid.appendChild(sk);
  }
  const data = await api('/api/apps');
  S.apps = data.apps;
  grid.innerHTML = '';
  if (!data.apps.length) {
    grid.appendChild(el('div', 'empty', '수집된 앱이 없습니다.'));
    return;
  }
  data.apps.forEach((app) => grid.appendChild(appCard(app)));
}

/* ------------------------------------------------------------- step list */

function actionLabel(row) {
  /* 좌표는 device px 그대로 — 수집이 기록한 값이고, export 가 프레임으로 옮기기
     전의 숫자다. 여기서 변환하면 화면과 export 가 다른 말을 하게 된다. */
  const a = row.action || {};
  const kind = a.action_type || '?';
  if (kind === 'tap' || kind === 'long_press') return `${kind} (${a.x}, ${a.y})`;
  if (kind === 'swipe') return `swipe (${a.x1}, ${a.y1}) → (${a.x2}, ${a.y2})`;
  if (kind === 'input_text') return `input_text "${a.text ?? ''}"`;
  return kind;
}

function stepCard(row) {
  const card = el('a', `step-card is-${row.status}`);
  card.href = `#/app/${encodeURIComponent(S.pkg)}/step/${row.step}`;
  const pair = el('div', 'pair');
  pair.append(
    shot(S.pkg, row.before, { tag: 'before', mark: row.mark, size: row.before_size }),
    shot(S.pkg, row.after, { tag: 'after' }),
  );
  card.appendChild(pair);
  if (row.group_size > 1) {
    card.appendChild(el('div', 'dupe', `×${row.group_size}`));
  }
  const meta = el('div', 'meta');
  meta.appendChild(el('span', 'mono', `#${row.step}`));
  meta.appendChild(el('span', 'mono', actionLabel(row)));
  if (row.drop) meta.appendChild(el('span', 'badge flat', S.meta.drops[row.drop] || row.drop));
  if (row.verdict && row.verdict.reason) {
    meta.appendChild(el('span', 'badge flat', S.meta.reasons[row.verdict.reason] || row.verdict.reason));
  }
  card.appendChild(meta);
  return card;
}

function toolbar() {
  const bar = el('div', 'toolbar');
  const scope = el('div', 'group');
  scope.appendChild(el('label', null, '범위'));
  [['export', 'export 대상'], ['all', '수집 전부']].forEach(([value, text]) => {
    const b = el('button', `pill tiny ${S.scope === value ? 'on' : 'ghost'}`, text);
    b.onclick = () => { S.scope = value; loadRows(); };
    scope.appendChild(b);
  });
  bar.appendChild(scope);

  const status = el('div', 'group');
  status.appendChild(el('label', null, '상태'));
  [['', '전체'], ['unreviewed', '미검토'], ['keep', '유지'], ['exclude', '제외']].forEach(([value, text]) => {
    const n = value ? (S.counts[value] ?? '') : '';
    const b = el('button', `pill tiny ${S.status === value ? 'on' : 'ghost'}`,
      value ? `${text} ${n}` : text);
    b.onclick = () => { S.status = value; loadRows(); };
    status.appendChild(b);
  });
  bar.appendChild(status);

  const collapse = el('button', `pill tiny ${S.collapse ? 'on' : 'ghost'}`, '중복 접기');
  collapse.onclick = () => { S.collapse = !S.collapse; loadRows(); };
  bar.appendChild(collapse);

  bar.appendChild(el('div', 'spacer'));
  const s = S.summary;
  bar.appendChild(el('span', 'mono',
    `${s.exportable ?? 0} export · 서로 다른 ${s.distinct ?? 0} · 중복 ${s.redundant ?? 0}`));
  return bar;
}

async function loadRows() {
  const view = $('#view');
  await loadRowsQuietly();
  view.innerHTML = '';
  view.className = 'view view-in';

  const head = el('div');
  head.appendChild(Object.assign(el('h1', 'display'), { textContent: S.pkg }));
  const lede = el('p', 'lede');
  const ratio = S.summary.exportable ? S.summary.redundant / S.summary.exportable : 0;
  lede.innerHTML = ratio >= 0.3
    ? `<b>이 앱은 서로 다른 화면을 ${S.summary.distinct}개밖에 보지 못했다.</b> ` +
      `export 대상 ${S.summary.exportable}건 중 ${S.summary.redundant}건(${Math.round(ratio * 100)}%)이 ` +
      `이미 있는 레코드의 반복이다. 필터로 줄이는 것보다 다시 수집하는 편이 맞을 수 있다.`
    : `export 대상 ${S.summary.exportable}건, 서로 다른 레코드 ${S.summary.distinct}건.`;
  head.appendChild(lede);
  view.appendChild(head);
  view.appendChild(toolbar());

  const grid = el('div', 'steps');
  if (!S.rows.length) {
    grid.appendChild(el('div', 'empty', '조건에 맞는 스텝이 없습니다.'));
  } else {
    S.rows.forEach((row) => grid.appendChild(stepCard(row)));
  }
  view.appendChild(grid);
  if (S.total > S.rows.length) {
    const more = el('div', 'more');
    more.appendChild(el('span', 'mono', `${S.total}건 중 ${S.rows.length}건`));
    const button = el('button', 'pill solid', '더 불러오기');
    button.onclick = () => { S.limit = Math.min(2000, S.limit + 400); loadRows(); };
    more.appendChild(button);
    view.appendChild(more);
  }
}

async function viewApp(pkg) {
  S.pkg = pkg;
  S.step = null;
  crumbs([{ text: pkg }]);
  $('#rules-open').hidden = false;
  await loadRows();
}

/* ---------------------------------------------------------------- detail */

function lineDiff(a, b) {
  /* 짧은 LCS. 화면 하나의 XML 은 수백 줄이라 O(n·m) 로 충분하다. */
  const A = a.split('\n');
  const B = b.split('\n');
  const n = A.length;
  const m = B.length;
  if (n * m > 4000000) return null;
  const table = new Uint32Array((n + 1) * (m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * (m + 1) + j] = A[i] === B[j]
        ? table[(i + 1) * (m + 1) + j + 1] + 1
        : Math.max(table[(i + 1) * (m + 1) + j], table[i * (m + 1) + j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push(['same', A[i]]); i += 1; j += 1; }
    else if (table[(i + 1) * (m + 1) + j] >= table[i * (m + 1) + j + 1]) { out.push(['del', A[i]]); i += 1; }
    else { out.push(['add', B[j]]); j += 1; }
  }
  while (i < n) { out.push(['del', A[i]]); i += 1; }
  while (j < m) { out.push(['add', B[j]]); j += 1; }
  return out;
}

function xmlPanel() {
  const d = S.detail;
  const panel = el('div', 'panel');
  const tabs = el('div', 'xml-tabs');
  [['html', 'html-like (학습 입력)'], ['raw', 'raw uiautomator']].forEach(([mode, text]) => {
    const b = el('button', `pill tiny ${S.xmlMode === mode ? 'on' : 'ghost'}`, text);
    b.onclick = () => { S.xmlMode = mode; renderDetail(); };
    tabs.appendChild(b);
  });
  const dt = el('button', `pill tiny ${S.diff ? 'on' : 'ghost'}`, 'diff');
  dt.onclick = () => { S.diff = !S.diff; renderDetail(); };
  tabs.appendChild(dt);
  panel.appendChild(tabs);

  const before = S.xmlMode === 'html' ? d.before_screen.html : d.before_screen.raw;
  const after = S.xmlMode === 'html' ? d.after_screen.html : d.after_screen.raw;
  const box = el('div', 'xml');
  const pre = el('pre');
  if (S.diff) {
    const rows = lineDiff(before || '', after || '');
    if (!rows) {
      pre.textContent = after || '';
    } else {
      pre.innerHTML = rows.map(([kind, line]) =>
        `<span class="d-${kind}">${kind === 'add' ? '+ ' : kind === 'del' ? '- ' : '  '}${esc(line)}</span>`
      ).join('');
    }
  } else {
    pre.textContent = after || '';
  }
  box.appendChild(pre);
  panel.appendChild(box);

  if (d.same_html) {
    panel.appendChild(el('p', 'note',
      '⚠︎ before 와 after 의 html-like XML 이 완전히 같다 — 학습 타깃이 입력과 동일한 레코드다.'));
  }
  return panel;
}

function verdictPanel() {
  const d = S.detail;
  const panel = el('div', 'panel');
  panel.appendChild(el('h3', null, '판정'));

  const strip = el('div', 'action-strip');
  const chip = el('div', 'action-chip');
  chip.append(el('span', 'sq'), actionLabel(d));
  strip.appendChild(chip);
  strip.appendChild(el('span', 'badge flat', d.changed ? '화면 바뀜' : '화면 그대로'));
  strip.appendChild(el('span', 'badge flat', d.page_changed ? '페이지 바뀜' : `page ${d.from_page}`));
  if (d.group_size > 1) strip.appendChild(el('span', 'badge warn', `같은 레코드 ×${d.group_size}`));
  if (d.drop) strip.appendChild(el('span', 'badge flat', S.meta.drops[d.drop] || d.drop));
  panel.appendChild(strip);

  const row = el('div', 'verdict-row');
  const mk = (cls, text, fn) => { const b = el('button', cls, text); b.onclick = fn; return b; };
  row.appendChild(mk(`pill ${d.status === 'exclude' ? 'solid' : ''}`, '제외 (X)',
    () => { S.pendingReason = true; renderDetail(); }));
  row.appendChild(mk(`pill ${d.status === 'keep' ? 'accent' : ''}`, '유지 (O)',
    () => decide('keep')));
  row.appendChild(mk('pill ghost', '취소 (U)', () => decide('clear')));
  if (d.members && d.members.length > 1) {
    row.appendChild(mk('pill', `이 그룹 ${d.members.length}건 모두 제외`,
      () => decideGroup('exclude')));
    row.appendChild(mk('pill ghost', '그룹 모두 유지', () => decideGroup('keep')));
  }
  if (d.verdict) {
    row.appendChild(el('span', 'mono',
      `${d.verdict.reviewer} · ${(d.verdict.at || '').slice(0, 16)}${d.verdict.rule ? ` · rule:${d.verdict.rule}` : ''}`));
  }
  panel.appendChild(row);

  if (S.pendingReason || d.status === 'exclude') {
    const reasons = el('div', 'reasons');
    Object.entries(S.meta.reasons).forEach(([code, label], i) => {
      const on = d.verdict && d.verdict.reason === code;
      const b = el('button', on ? 'on' : '');
      b.innerHTML = `<kbd>${i + 1}</kbd>${esc(label)}`;
      b.onclick = () => decide('exclude', code);
      reasons.appendChild(b);
    });
    panel.appendChild(reasons);
    const note = el('input', 'note-input');
    note.placeholder = '메모 (선택)';
    note.value = (d.verdict && d.verdict.note) || '';
    note.id = 'note';
    panel.appendChild(note);
  }

  const kv = el('dl', 'kv');
  const add = (k, v) => { kv.append(el('dt', null, k), el('dd', 'mono', v)); };
  add('step', `#${d.step}  (before ${d.before} → after ${d.after})`);
  add('frame', `${d.before_size[0]}×${d.before_size[1]} → ${d.after_size[0]}×${d.after_size[1]} device px`);
  add('reason', d.reason || '—');
  add('key', d.key);
  if (d.coord_out_of_frame) add('경고', `액션 좌표 ${d.coord_out_of_frame}개가 프레임 밖`);
  panel.appendChild(kv);
  return panel;
}

function renderDetail() {
  const d = S.detail;
  const view = $('#view');
  view.className = 'view view-in';
  view.innerHTML = '';

  const nav = el('div', 'toolbar');
  const idx = S.rows.findIndex((r) => r.step === d.step);
  const prev = el('button', 'pill ghost', '← 이전 (K)');
  prev.onclick = () => hop(-1);
  const next = el('button', 'pill ghost', '다음 (J) →');
  next.onclick = () => hop(1);
  nav.append(prev, next);
  nav.appendChild(el('span', 'mono', idx >= 0 ? `${idx + 1} / ${S.rows.length}` : `#${d.step}`));
  nav.appendChild(el('div', 'spacer'));
  const blink = el('button', 'pill tiny ghost', 'Space 를 누르고 있으면 깜빡');
  blink.onmousedown = () => { S.blink = true; S.blinkFlip = true; applyBlink(); startBlink(); };
  blink.onmouseup = stopBlink;
  blink.onmouseleave = () => { if (S.blink) stopBlink(); };
  nav.appendChild(blink);
  const back = el('button', 'pill ghost', '목록 (G)');
  back.onclick = () => { location.hash = `#/app/${encodeURIComponent(S.pkg)}`; };
  nav.appendChild(back);
  view.appendChild(nav);

  const grid = el('div', 'detail');
  const left = el('div', 'panel');
  const frames = el('div', 'frames');
  frames.id = 'frames';

  /* before 위에 after 를 겹쳐 두고 class 만 토글한다. 깜빡일 때마다 다시 그리면
     XML diff(LCS)까지 매번 돌아 화면이 끊긴다. */
  const fa = el('div', 'frame a');
  const capA = el('div', 'cap');
  capA.append(el('span', null, 'before'), el('span', 'dim', `#${d.before}`));
  const stack = shot(S.pkg, d.before, {
    big: true, mark: d.mark, size: d.before_size, missing: !d.before_screen.has_shot,
  });
  if (d.after_screen.has_shot) {
    const overlay = el('img', 'blink-over');
    overlay.src = `/img/${S.pkg}/${d.after}`;
    overlay.alt = 'after';
    stack.appendChild(overlay);
  }
  fa.append(capA, stack);

  const fb = el('div', 'frame b');
  const capB = el('div', 'cap');
  capB.append(el('span', null, 'after'), el('span', 'dim', `#${d.after}`));
  fb.append(capB, shot(S.pkg, d.after, { big: true, missing: !d.after_screen.has_shot }));

  frames.append(fa, fb);
  left.appendChild(frames);
  const right = el('div', 'stack');
  right.append(verdictPanel(), xmlPanel());
  grid.append(left, right);
  view.appendChild(grid);
  applyBlink();
}

function applyBlink() {
  const frames = $('#frames');
  if (!frames) return;
  frames.classList.toggle('blinking', S.blink);
  frames.classList.toggle('flip', Boolean(S.blink && S.blinkFlip));
}

async function viewStep(pkg, step) {
  // The step must be IN the walking order, or J/K silently stop working. A
  // deep link, or a step reached from a collapsed group, is not.
  if (S.pkg !== pkg) { S.pkg = pkg; S.rows = []; }
  if (!S.rows.some((row) => row.step === step)) {
    await loadRowsQuietly(S.rows.length ? { collapse: '0' } : {});
    if (!S.rows.some((row) => row.step === step)) {
      await loadRowsQuietly({ collapse: '0', status: '', scope: 'all' });
    }
  }
  crumbs([{ text: pkg, href: `#/app/${encodeURIComponent(pkg)}` }, { text: `#${step}` }]);
  $('#rules-open').hidden = false;
  S.step = step;
  S.pendingReason = false;
  S.detail = await api(`/api/app/${encodeURIComponent(pkg)}/step/${step}`);
  renderDetail();
}

function hop(delta) {
  const idx = S.rows.findIndex((r) => r.step === S.step);
  if (idx < 0) return;
  const next = S.rows[idx + delta];
  if (!next) { toast(delta > 0 ? '마지막 스텝입니다' : '첫 스텝입니다'); return; }
  location.hash = `#/app/${encodeURIComponent(S.pkg)}/step/${next.step}`;
}

async function decide(verdict, reason) {
  const d = S.detail;
  if (!d) return;
  const noteField = $('#note');
  await post('/api/decide', {
    package: S.pkg, step: d.step, before: d.before, after: d.after,
    verdict, reason: reason || '', key: d.key,
    note: noteField ? noteField.value : '',
  });
  const row = S.rows.find((r) => r.step === d.step);
  const status = verdict === 'clear' ? 'unreviewed' : verdict;
  if (row) row.status = status;
  d.status = status;
  d.verdict = verdict === 'clear' ? null
    : { verdict, reason: reason || '', note: noteField ? noteField.value : '',
        reviewer: S.meta.reviewer, at: new Date().toISOString(), rule: '' };
  S.pendingReason = false;
  toast(verdict === 'clear' ? '판정 취소' : verdict === 'keep' ? '유지' : `제외 · ${S.meta.reasons[reason] || ''}`);
  if (verdict !== 'clear') { hop(1); } else { renderDetail(); }
}

async function decideGroup(verdict) {
  /* 같은 레코드로 접힌 스텝 전부에 같은 판정을 편다. 판정 자체는 여전히
     스텝 단위로 한 줄씩 기록된다 — 그룹은 보여주는 방식일 뿐이다. */
  const d = S.detail;
  if (!d || !d.members) return;
  const noteField = $('#note');
  const reason = verdict === 'exclude'
    ? ((d.verdict && d.verdict.reason) || 'no_effect') : '';
  await post('/api/decide', {
    rows: d.members.map((step) => ({
      package: S.pkg, step, verdict, reason,
      note: noteField ? noteField.value : '', rule: 'group',
    })),
  });
  toast(`${d.members.length}건 ${verdict === 'exclude' ? '제외' : '유지'}`);
  // 다음 스텝은 목록을 다시 받기 전에 정해 둔다. '미검토' 필터가 걸려 있으면
  // 방금 판정한 스텝들이 목록에서 사라져 hop() 이 자기 위치를 못 찾고,
  // 화면이 그 자리에 멈춘 채 J/K 가 죽는다.
  const idx = S.rows.findIndex((row) => row.step === d.step);
  const next = S.rows[idx + 1];
  await loadRowsQuietly();
  if (next) {
    location.hash = `#/app/${encodeURIComponent(S.pkg)}/step/${next.step}`;
  } else {
    S.detail.status = verdict === 'exclude' ? 'exclude' : 'keep';
    renderDetail();
  }
}

async function loadRowsQuietly(overrides) {
  const params = new URLSearchParams(Object.assign({
    scope: S.scope, status: S.status, collapse: S.collapse ? '1' : '0', limit: String(S.limit),
  }, overrides || {}));
  const data = await api(`/api/app/${encodeURIComponent(S.pkg)}?${params}`);
  S.rows = data.rows;
  S.counts = data.counts;
  S.summary = data.summary;
  S.total = data.total;
  return data;
}

/* ----------------------------------------------------------------- rules */

async function renderRules() {
  const body = $('#rules-body');
  body.innerHTML = '';
  S.meta.rules.forEach((rule) => {
    const card = el('div', 'rule');
    card.appendChild(el('h4', null, rule.title));
    card.appendChild(el('p', null, rule.detail));
    const opts = el('div', 'opts');
    const inputs = {};
    Object.entries(rule.options || {}).forEach(([name, spec]) => {
      opts.appendChild(el('span', null, name === 'keep' ? '남길 개수' : name === 'nodes' ? '노드 임계' : name));
      const input = el('input');
      input.type = 'number';
      input.value = spec.default;
      if (spec.min != null) input.min = spec.min;
      if (spec.max != null) input.max = spec.max;
      inputs[name] = input;
      opts.appendChild(input);
    });
    if (opts.childNodes.length) card.appendChild(opts);

    const acts = el('div', 'acts');
    const count = el('span', 'count', '—');
    const read = () => Object.fromEntries(
      Object.entries(inputs).map(([k, i]) => [k, Number(i.value)]));
    const preview = el('button', 'pill tiny ghost', '미리보기');
    preview.onclick = async () => {
      const data = await post('/api/rules/preview',
        { package: S.pkg, rule: rule.id, options: read() });
      count.textContent = `${data.matched}건`;
      apply.hidden = data.matched === 0;
    };
    const apply = el('button', 'pill tiny solid', '제외 적용');
    apply.hidden = true;
    apply.onclick = async () => {
      const data = await post('/api/rules/apply',
        { package: S.pkg, rule: rule.id, options: read() });
      toast(`${data.applied}건 제외${data.skipped ? ` · 이미 판정된 ${data.skipped}건은 유지` : ''}`);
      apply.hidden = true;
      count.textContent = '적용됨';
      loadRows();
    };
    const undo = el('button', 'pill tiny ghost', '이 룰 되돌리기');
    undo.onclick = async () => {
      const data = await post('/api/rules/undo', { package: S.pkg, rule: rule.id });
      toast(`${data.cleared}건 되돌림`);
      count.textContent = '—';
      loadRows();
    };
    acts.append(preview, apply, undo, count);
    card.appendChild(acts);
    body.appendChild(card);
  });
}

function openRules(open) {
  $('#rules').hidden = !open;
  $('#scrim').hidden = !open;
  if (open) renderRules();
}

/* ---------------------------------------------------------------- router */

function crumbs(parts) {
  const bar = $('#crumbs');
  bar.innerHTML = '';
  parts.forEach((part, i) => {
    if (i) bar.appendChild(el('span', 'sep', '/'));
    if (part.href) {
      const a = el('a', 'dim', part.text);
      a.href = part.href;
      bar.appendChild(a);
    } else {
      bar.appendChild(el('span', 'now', part.text));
    }
  });
}

async function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  const parts = hash.split('/').filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] === 'app' && parts[2] === 'step') return await viewStep(parts[1], Number(parts[3]));
    if (parts[0] === 'app') return await viewApp(parts[1]);
    return await viewApps();
  } catch (error) {
    $('#view').innerHTML = '';
    $('#view').appendChild(el('div', 'empty', String(error.message || error)));
  }
}

/* -------------------------------------------------------------- keyboard */

document.addEventListener('keydown', (event) => {
  if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') {
    if (event.key === 'Escape') event.target.blur();
    return;
  }
  const inDetail = Boolean(S.detail && S.step != null);
  if (event.key === '?') { $('#help').showModal(); return; }
  if (event.key === 'Escape') { openRules(false); if (inDetail) location.hash = `#/app/${encodeURIComponent(S.pkg)}`; return; }
  if (!inDetail) return;
  const key = event.key.toLowerCase();
  if (key === 'j' || event.key === 'ArrowRight') { event.preventDefault(); hop(1); }
  else if (key === 'k' || event.key === 'ArrowLeft') { event.preventDefault(); hop(-1); }
  else if (key === 'x') { S.pendingReason = true; renderDetail(); }
  else if (key === 'o' || event.key === 'Enter') { decide('keep'); }
  else if (key === 'u') { decide('clear'); }
  else if (key === 'r') { S.xmlMode = S.xmlMode === 'html' ? 'raw' : 'html'; renderDetail(); }
  else if (key === 'd') { S.diff = !S.diff; renderDetail(); }
  else if (key === 'g') { location.hash = `#/app/${encodeURIComponent(S.pkg)}`; }
  else if (event.code === 'Space') {
    event.preventDefault();
    if (!S.blink) { S.blink = true; S.blinkFlip = true; applyBlink(); startBlink(); }
  } else if (/^[1-7]$/.test(event.key)) {
    const code = Object.keys(S.meta.reasons)[Number(event.key) - 1];
    if (code) decide('exclude', code);
  }
});

let blinkTimer = null;
function startBlink() {
  clearInterval(blinkTimer);
  blinkTimer = setInterval(() => {
    if (!S.blink) { clearInterval(blinkTimer); return; }
    S.blinkFlip = !S.blinkFlip;
    applyBlink();
  }, 420);
}
function stopBlink() {
  S.blink = false;
  S.blinkFlip = false;
  clearInterval(blinkTimer);
  applyBlink();
}
document.addEventListener('keyup', (event) => {
  if (event.code === 'Space' && S.blink) stopBlink();
});
window.addEventListener('blur', () => { if (S.blink) stopBlink(); });

/* ------------------------------------------------------------------ boot */

window.addEventListener('hashchange', route);
$('#rules-open').onclick = () => openRules(true);
$('#rules-close').onclick = () => openRules(false);
$('#scrim').onclick = () => openRules(false);
$('#help-open').onclick = () => $('#help').showModal();
$('#help-close').onclick = () => $('#help').close();

(async () => {
  S.meta = await api('/api/meta');
  $('#reviewer').textContent = S.meta.reviewer;
  await route();
})();

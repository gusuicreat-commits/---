/* 产品参数校验 · 前端（无框架，hash 路由） */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const app = $('#app');

const KINDS = { drum: 'drum_param', travel_drive: 'travel_drive_param', control_cabinet: 'control_cabinet_param', travel_paint: 'travel_paint_param' };
const KIND_LABEL = { drum: '卷筒', travel_drive: '大车传动', control_cabinet: '控制柜', travel_paint: '大车油漆' };
const STATUS_CLS = { '合格': 'ok', '不合格': 'ng', '待校验': 'wait', '未录全': 'part' };
const STATUSES = ['未录全', '待校验', '不合格', '合格'];

const ICON = {
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  dl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>',
  back: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m15 18-6-6 6-6"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m9 18 6-6-6-6"/></svg>',
};

/* ---------------- 接口 ---------------- */
class ApiError extends Error {
  constructor(status, err) { super(err.message || '请求失败'); this.status = status; Object.assign(this, err); }
}
async function api(method, path, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch('/api' + path, opt); }
  catch (e) { throw new ApiError(0, { code: 'NETWORK', message: '连不上服务，请确认主程序已启动' }); }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) {
    if (data && data.error) throw new ApiError(res.status, data.error);
    if (res.status === 404) throw new ApiError(404, { code: 'NOT_IMPLEMENTED', message: '该功能接口尚未上线' });
    throw new ApiError(res.status, { code: 'HTTP', message: `服务出错（${res.status}）` });
  }
  return data;
}

let toastT;
function toast(msg, cls = '') {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast on ' + cls;
  clearTimeout(toastT); toastT = setTimeout(() => t.className = 'toast', 3200);
}
function confirmBox(title, text, okText = '确定', danger = false) {
  return new Promise(res => {
    const m = document.createElement('div'); m.className = 'modal';
    m.innerHTML = `<div class="card"><h3>${esc(title)}</h3><p>${esc(text)}</p>
      <div class="actions"><span class="sp"></span><button class="btn ghost" data-v="0">取消</button><button class="btn ${danger ? 'danger' : 'pri'}" data-v="1">${esc(okText)}</button></div></div>`;
    m.addEventListener('click', e => { const v = e.target.dataset.v; if (v !== undefined || e.target === m) { m.remove(); res(v === '1'); } });
    document.body.appendChild(m);
  });
}

/* ---------------- 公共数据 ---------------- */
const G = { fields: null, rules: null, voice: { available: false } };
async function loadFields() { if (!G.fields) G.fields = await api('GET', '/fields'); QC.G.fields=G.fields; return G.fields; }
async function loadRules() { G.rules = await api('GET', '/rules'); QC.G.rules=G.rules; return G.rules; }
function ruleOf(table, name) { return (G.rules?.items || []).find(r => r.table_name === table && r.name === name); }
function rangeText(r) {
  if (!r || r.min == null || r.max == null || r.min === '' || r.max === '') return '规则未配置';
  return `${r.min}～${r.max}`;
}
function inRange(v, r) {
  if (v == null || v === '' || !r || r.min == null || r.max == null) return null;
  const x = Number(v); return x >= Number(r.min) && x <= Number(r.max);
}
const pill = s => `<span class="pill ${STATUS_CLS[s] || 'part'}">${esc(s)}</span>`;
const qPill = q => q === 1 ? '<span class="pill ok">合格</span>' : q === 0 ? '<span class="pill ng">不合格</span>' : '<span class="pill wait">待校验</span>';

async function refreshVoice() {
  const el = $('#vstat');
  try {
    const s = await api('GET', '/voice/status');
    G.voice = s;
    if (s.available && s.loaded) el.innerHTML = `<span class="dot ok"></span><span class="lbl">语音就绪${s.denoise_enabled ? ' · 降噪开' : ''}</span>`;
    else if (s.available) el.innerHTML = '<span class="dot warn"></span><span class="lbl">语音模型加载中</span>';
    else el.innerHTML = '<span class="dot bad"></span><span class="lbl">语音不可用</span>';
  } catch (e) {
    G.voice = { available: false, missing: e.code === 'NOT_IMPLEMENTED' };
    el.innerHTML = '<span class="dot bad"></span><span class="lbl">语音不可用</span>';
  }
  document.dispatchEvent(new Event('voicestatus'));
}

/* ---------------- 路由 ---------------- */
const routes = [
  [/^#?\/?$/, pageList],
  [/^#\/new$/, () => pageContractForm(null)],
  [/^#\/rules$/, pageRules],
  [/^#\/exports$/, pageExports],
  [/^#\/c\/([^/]+)$/, m => pageDetail(decodeURIComponent(m[1]))],
  [/^#\/c\/([^/]+)\/edit$/, m => pageContractForm(decodeURIComponent(m[1]))],
  [/^#\/c\/([^/]+)\/(drum|travel_drive|control_cabinet|travel_paint)$/, m => pageEntry(decodeURIComponent(m[1]), m[2])],
];
let cleanup = null;
async function route() {
  if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; }
  const h = location.hash || '#/';
  const navK = h.startsWith('#/rules') ? 'rules' : h.startsWith('#/exports') ? 'exports' : 'list';
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.k === navK));
  for (const [re, fn] of routes) {
    const m = h.match(re);
    if (m) {
      app.innerHTML = '<div class="skeleton"></div>';
      try { await fn(m); }
      catch (e) { console.error(e); app.innerHTML = `<div class="empty"><b>${esc(e.message || '页面加载失败')}</b><a class="btn" href="#/" style="margin-top:14px">返回合同列表</a></div>`; }
      window.scrollTo(0, 0);
      return;
    }
  }
  location.hash = '#/';
}
window.addEventListener('hashchange', route);

/* ================= 合同列表 ================= */
async function pageList() {
  const st = { q: '', status: '' };
  const [all, rules] = await Promise.all([api('GET', '/contracts'), loadRules()]);
  const counts = {}; all.items.forEach(c => counts[c.status] = (counts[c.status] || 0) + 1);
  app.innerHTML = `
    <div class="ph"><div><h1>合同</h1><div class="sub">按合同号录入卷筒、大车传动、控制柜与大车油漆参数，自动判定是否合格</div></div><span class="sp"></span>
      <a class="btn pri" href="#/new">${ICON.plus}新建合同</a></div>
    ${rules.configured < rules.total ? `<div class="note amb">规则已配置 ${rules.configured}/${rules.total}：未配置的参数会被判为不合格。<a href="#/rules">去配置规则</a></div>` : ''}
    <div class="grid4">
      <div class="kpi"><div class="micro">合同总数</div><b>${all.items.length}</b><span>全部</span></div>
      <div class="kpi"><div class="micro">可导出</div><b style="color:var(--lime)">${counts['合格'] || 0}</b><span>四类参数最新记录均合格</span></div>
      <div class="kpi"><div class="micro">不合格</div><b style="color:var(--red)">${counts['不合格'] || 0}</b><span>需复测后重新录入</span></div>
      <div class="kpi"><div class="micro">未完成</div><b style="color:var(--amber)">${(counts['未录全'] || 0) + (counts['待校验'] || 0)}</b><span>未录全 ${counts['未录全'] || 0} · 待校验 ${counts['待校验'] || 0}</span></div>
    </div>
    <div class="bar">
      <div class="search">${ICON.search}<input id="q" placeholder="搜索合同号、客户名称或日期" autocomplete="off"></div>
      <div class="chips" id="chips">
        <button class="chip on" data-s="">全部<em>${all.items.length}</em></button>
        ${STATUSES.map(s => `<button class="chip" data-s="${s}">${s}<em>${counts[s] || 0}</em></button>`).join('')}
      </div>
    </div>
    <div class="card" style="padding:10px 10px 4px"><div id="tb"></div></div>`;
  const draw = items => {
    $('#tb').innerHTML = items.length ? `<table class="tbl"><thead><tr><th>合同号</th><th>客户名称</th><th class="hide-s">录入日期</th><th>状态</th><th class="hide-s">说明</th><th></th></tr></thead><tbody>
      ${items.map(c => `<tr class="link" data-ht="${esc(c.ht_code)}"><td class="ht">${esc(c.ht_code)}</td><td>${esc(c.name)}</td><td class="mono hide-s" style="color:var(--tx2)">${esc(c.date)}</td>
        <td>${pill(c.status)}</td><td class="hide-s" style="color:var(--tx3);font-size:12.5px">${esc(c.status_reason || '')}</td><td class="r" style="color:var(--tx3)">${ICON.arrow}</td></tr>`).join('')}
      </tbody></table>` : `<div class="empty"><b>${st.q || st.status ? '没有符合条件的合同' : '还没有合同'}</b>${st.q || st.status ? '换个关键词或状态试试' : '点右上角「新建合同」开始'}</div>`;
    $$('#tb tr.link').forEach(tr => tr.onclick = () => location.hash = '#/c/' + encodeURIComponent(tr.dataset.ht));
  };
  draw(all.items);
  let t;
  const reload = async () => {
    const p = new URLSearchParams(); if (st.q) p.set('q', st.q); if (st.status) p.set('status', st.status);
    const r = await api('GET', '/contracts?' + p); draw(r.items);
  };
  $('#q').oninput = e => { st.q = e.target.value.trim(); clearTimeout(t); t = setTimeout(reload, 220); };
  $$('#chips .chip').forEach(b => b.onclick = () => { $$('#chips .chip').forEach(x => x.classList.remove('on')); b.classList.add('on'); st.status = b.dataset.s; reload(); });
}

/* ================= 新建 / 编辑合同 ================= */
async function pageContractForm(ht) {
  const edit = !!ht;
  let c = { ht_code: '', name: '', date: '' };
  if (edit) c = (await api('GET', '/contracts/' + encodeURIComponent(ht))).contract;
  else {
    const s = await api('GET', '/contracts/suggest');
    c.ht_code = s.ht_code || '';
    c.date = todayStr();
  }
  app.innerHTML = `
    <a class="crumb" href="${edit ? '#/c/' + encodeURIComponent(ht) : '#/'}">${ICON.back}${edit ? '返回合同详情' : '合同列表'}</a>
    <div class="ph"><div><h1>${edit ? '编辑合同' : '新建合同'}</h1><div class="sub">${edit ? '合同号不能修改；客户名称和录入日期可以改' : '合同号已按「年份＋4位随机数字」自动填好，可以修改'}</div></div></div>
    <div class="grid2 entry-wrap" style="grid-template-columns:1.35fr 1fr;align-items:start">
      <div class="card">
        <form class="form" id="f" novalidate>
          ${fieldHTML('ht_code', '合同号', '', c.ht_code, { mono: true, ro: edit, ph: '20264827', hint: edit ? '合同号是参数记录的关联依据，不能修改' : '格式：4位年份＋4位数字，例如 20264827；随机部分可含前导零' })}
          ${fieldHTML('name', '客户名称', '', c.name, { text: true, ph: '例如：某某重工有限公司' })}
          ${fieldHTML('date', '录入日期', '', c.date, { mono: true, ph: 'YYYY-MM-DD', hint: '默认今天，可以修改；合同号不再包含月日' })}
          <div class="actions"><span class="sp"></span><a class="btn ghost" href="${edit ? '#/c/' + encodeURIComponent(ht) : '#/'}">取消</a><button class="btn pri" type="submit">${edit ? '保存' : '创建合同'}</button></div>
        </form>
      </div>
      ${voicePanelHTML('contract')}
    </div>`;
  const f = $('#f');
  cleanup = mountVoice('contract', f, edit ? ['ht_code'] : []);
  f.onsubmit = async e => {
    e.preventDefault(); clearErrs(f);
    const body = { name: f.name.value.trim(), date: f.date.value.trim() || undefined };
    if (!body.name) return setErr(f, 'name', '客户名称不能为空');
    if (body.date && !/^\d{4}-\d{2}-\d{2}$/.test(body.date)) return setErr(f, 'date', '日期格式应为 YYYY-MM-DD');
    try {
      if (edit) {
        await api('PATCH', '/contracts/' + encodeURIComponent(ht), body);
        toast('已保存', 'ok'); location.hash = '#/c/' + encodeURIComponent(ht);
      } else {
        body.ht_code = f.ht_code.value.trim();
        const r = await api('POST', '/contracts', body);
        toast('合同已创建', 'ok'); location.hash = '#/c/' + encodeURIComponent(r.ht_code);
      }
    } catch (err) {
      if (['FORMAT', 'YEAR_INVALID', 'DATE_LEN', 'DATE_INVALID', 'SEQ_INVALID', 'DUPLICATE'].includes(err.code)) {
        setErr(f, 'ht_code', err.message + (err.suggestion ? `，可用：${err.suggestion}` : ''));
        if (err.suggestion) { f.ht_code.value = err.suggestion; }
      } else if (err.code?.startsWith('NAME')) setErr(f, 'name', err.message);
      else toast(err.message, 'ng');
    }
  };
}
function todayStr() {
  const d = new Date(Date.now() + 8 * 3600e3); // 北京时间
  return d.toISOString().slice(0, 10);
}
function fieldHTML(name, label, unit, value, o = {}) {
  return `<div class="fld" data-f="${name}">
    <label>${esc(label)}${unit ? `<span class="u">${esc(unit)}</span>` : ''}${o.rule ? `<span class="rule">${esc(o.rule)}</span>` : ''}</label>
    <div class="inp"><input name="${name}" class="${o.text ? 'txt' : ''}" value="${esc(value ?? '')}" placeholder="${esc(o.ph || '')}" ${o.ro ? 'readonly style="opacity:.55"' : ''}
      ${o.num ? 'inputmode="decimal"' : ''} autocomplete="off">${unit ? `<span class="unit">${esc(unit)}</span>` : ''}</div>
    <div class="hint" data-base="${esc(o.hint || '')}">${esc(o.hint || '')}</div></div>`;
}
function setErr(f, name, msg) {
  const d = $(`.fld[data-f="${name}"]`, f); if (!d) return toast(msg, 'ng');
  d.classList.remove('voice', 'key'); d.classList.add('err'); $('.hint', d).textContent = msg; $('input', d).focus();
}
function clearErrs(f) { $$('.fld.err', f).forEach(d => { d.classList.remove('err'); $('.hint', d).textContent = $('.hint', d).dataset.base; }); }

/* ================= 合同详情 ================= */
async function pageDetail(ht) {
  const [d, fields] = await Promise.all([api('GET', '/contracts/' + encodeURIComponent(ht)), loadFields(), loadRules()]);
  const c = d.contract, enc = encodeURIComponent(c.ht_code);
  const hasRec = Object.values(d.latest).some(Boolean);
  app.innerHTML = `
    <a class="crumb" href="#/">${ICON.back}合同列表</a>
    <div class="chead">
      <div><div class="micro" style="margin-bottom:6px">合同号</div><div class="ht">${esc(c.ht_code)}</div></div>
      <div class="meta"><span>客户 <b>${esc(c.name)}</b></span><span>录入日期 <b class="mono">${esc(c.date)}</b></span></div>
      <span class="sp"></span>
      <a class="btn sm ghost" href="#/c/${enc}/edit">编辑</a>
      ${hasRec ? '' : '<button class="btn sm danger" id="del">删除</button>'}
    </div>
    ${d.rules.configured < d.rules.total ? `<div class="note amb">规则已配置 ${d.rules.configured}/${d.rules.total}：未配置的参数会被判为不合格，这不是参数本身的问题。<a href="#/rules">去配置规则</a></div>` : ''}
    <div class="gate">
      ${pill(d.status)}
      <div class="t">${d.status === '合格' ? '四类参数最新记录均合格，可以导出' : esc(d.status_reason)}<small>只有「合格」的合同可以导出 Excel；每类参数以最新一条为准</small></div>
      <span class="sp"></span>
      <button class="btn sm ghost" id="reval" title="按当前规则重新判定该合同的全部记录">重新校验</button>
      <button class="btn pri" id="exp" ${d.status === '合格' ? '' : 'disabled'}>${ICON.dl}导出 Excel</button>
    </div>
    <div id="expres"></div>
    <div class="grid2">${Object.keys(KINDS).map(k => paramCardHTML(k, d.latest[k], fields)).join('')}</div>`;

  $$('[data-hist]').forEach(b => b.onclick = async () => {
    const k = b.dataset.hist, box = $('#hist-' + k);
    if (box.classList.toggle('on') && !box.dataset.loaded) {
      const r = await api('GET', `/contracts/${enc}/records/${k}`);
      box.dataset.loaded = 1;
      const fl = fields[KINDS[k]].fields;
      box.innerHTML = r.items.map((x, i) => `<div class="h"><span class="mono">#${x.id}</span>
        <span>${fl.map(f => x[f.name] != null && x[f.name] !== '' ? `${esc(f.label)} <span class="mono" style="color:var(--tx)">${esc(x[f.name])}</span>` : '').filter(Boolean).join('　')}
          <span class="mono" style="margin-left:8px">${esc(x.created_at)}</span>${i === 0 ? ' <span class="tag">最新</span>' : ''}</span>${qPill(x.is_qualified)}</div>`).join('') || '<div class="h">暂无记录</div>';
    }
  });
  $('#reval').onclick = async () => {
    try { const r = await api('POST', `/contracts/${enc}/revalidate`); toast(`已按当前规则重新校验 ${r.records} 条记录：${r.contract_status.status}`, 'ok'); pageDetail(ht); }
    catch (e) { toast(e.message, 'ng'); }
  };
  $('#exp').onclick = async () => {
    const b = $('#exp'); b.disabled = true; b.textContent = '正在导出…';
    try { const r = await api('POST', `/contracts/${enc}/export`); $('#expres').innerHTML = exportResultHTML(r); toast('导出完成', 'ok'); }
    catch (e) { toast(e.message, 'ng'); }
    b.disabled = false; b.innerHTML = ICON.dl + '导出 Excel';
  };
  const del = $('#del');
  if (del) del.onclick = async () => {
    if (!await confirmBox('删除合同', `确定删除合同 ${c.ht_code}？还没有任何参数记录，删除后不可恢复。`, '删除', true)) return;
    try { await api('DELETE', '/contracts/' + enc); toast('已删除', 'ok'); location.hash = '#/'; } catch (e) { toast(e.message, 'ng'); }
  };
}
function paramCardHTML(kind, rec, fields) {
  const table = KINDS[kind], fl = fields[table].fields, enc = encodeURIComponent(location.hash.split('/')[2] || '');
  const head = `<div class="hd"><h3>${KIND_LABEL[kind]}参数</h3>${rec ? qPill(rec.is_qualified) : '<span class="pill part">未录入</span>'}<span class="sp"></span>
    <a class="btn sm ${rec ? '' : 'pri'}" href="#/c/${enc}/${kind}">${rec ? '复测 / 重新录入' : '录入'}</a></div>`;
  if (!rec) return `<div class="card pcard">${head}<div class="empty" style="padding:36px 10px"><b>还没有${KIND_LABEL[kind]}参数</b>点「录入」手动填写或用语音填写</div></div>`;
  return `<div class="card pcard">${head}<div class="meas">
    ${fl.map(f => {
      const v = rec[f.name];
      if (f.type === 'text' && !f.numeric_list) return `<div class="m txt"><span class="k">${esc(f.label)}</span><span class="v">${v ? esc(v) : '<span style="color:var(--tx3)">—</span>'}</span><span class="rg">不校验</span></div>`;
      if (f.numeric_list) { const j=QC.prejudge(kind,f.name,v); return `<div class="m ${j.cls==='ng'?'bad':''}"><span class="k">${esc(f.label)}</span><span class="v">${esc(v??'—')}</span><span class="rg" style="white-space:normal">${esc(j.verdict)}</span></div>`; }
      const r = ruleOf(table, f.name), ok = inRange(v, r);
      return `<div class="m ${ok === false || v == null ? 'bad' : ''}"><span class="k">${esc(f.label)}</span><span class="v">${v != null && v !== '' ? esc(v) : '—'}<small>${esc(f.unit || '')}</small></span><span class="rg">${esc(rangeText(r))}</span></div>`;
    }).join('')}</div>
    <div class="foot"><span class="mono">#${rec.id} · ${esc(rec.created_at)}</span><span class="sp"></span><button class="btn sm ghost" data-hist="${kind}">历史记录</button></div>
    <div class="hist" id="hist-${kind}"></div></div>`;
}
function exportResultHTML(r) {
  const d = (r.file.match(/_(\d{8})\.xlsx$/) || [])[1] || '';
  return `<div class="note lime" style="flex-wrap:wrap"><span>已导出 <b class="mono">${esc(r.file)}</b></span>
    <span class="shs">${r.sheets.map(s => `<span class="${s.ok ? '' : 'bad'}" title="${esc(s.error || '')}">${esc(s.name)}${s.ok ? '' : ' · 失败'}</span>`).join('')}</span>
    <a href="/api/exports/${encodeURIComponent(r.ht_code)}/${d}">下载</a></div>`;
}

/* ================= 参数录入 ================= */
async function pageEntry(ht, kind) {
  const [d, fields] = await Promise.all([api('GET', '/contracts/' + encodeURIComponent(ht)), loadFields(), loadRules()]);
  const c = d.contract, enc = encodeURIComponent(c.ht_code), table = KINDS[kind], fl = fields[table].fields;
  const prev = d.latest[kind];
  app.innerHTML = `
    <a class="crumb" href="#/c/${enc}">${ICON.back}${esc(c.ht_code)} · ${esc(c.name)}</a>
    <div class="ph"><div><h1>录入${KIND_LABEL[kind]}参数</h1><div class="sub">每次提交新增一条记录，以最新一条为准；${prev ? '这次提交会作为复测结果' : '提交后立即按规则判定'}</div></div></div>
    ${G.rules.configured < G.rules.total ? `<div class="note amb">规则已配置 ${G.rules.configured}/${G.rules.total}：未配置的参数会被判为不合格。<a href="#/rules">去配置规则</a></div>` : ''}
    <div id="verdict"></div>
    <div class="grid2 entry-wrap" style="grid-template-columns:1.35fr 1fr;align-items:start">
      <div class="card">
        <div class="hd"><h3>合同号 <span class="mono" style="font-weight:400;color:var(--tx2)">${esc(c.ht_code)}</span></h3><span class="sp"></span><span class="micro">${esc(KIND_LABEL[kind])}</span></div>
        <form class="form" id="f" novalidate>
          <div class="formgrid">
          ${fl.map(f => f.type === 'text'
            ? `<div style="grid-column:1/-1">${fieldHTML(f.name, f.label, '', '', { text: true, ph: f.numeric_list ? '例如：98,97,96' : '自由文本，不参与校验', hint: '最长 ' + (f.max_len || 255) + ' 字' })}</div>`
            : fieldHTML(f.name, f.label, f.unit, '', { num: true, ph: '0.00', rule: rangeText(ruleOf(table, f.name)) })).join('')}
          </div>
          <div class="actions"><button class="btn ghost" type="button" id="clr">清空</button><span class="sp"></span><a class="btn ghost" href="#/c/${enc}">取消</a><button class="btn pri" type="submit">提交并校验</button></div>
        </form>
      </div>
      ${voicePanelHTML(kind)}
    </div>`;
  const f = $('#f');
  cleanup = mountVoice(kind, f, []);
  QC.G.fields = fields; QC.G.rules = G.rules;
  fl.filter(fd=>fd.numeric_list).forEach(fd=>{
    const input=f[fd.name], wrap=input.closest('.fld');
    input.addEventListener('input',()=>{const j=QC.prejudge(kind,fd.name,input.value);wrap.classList.toggle('err',j.cls==='ng');wrap.querySelector('.hint').textContent=j.verdict;});
    wrap.querySelector('.hint').textContent='多个测点用逗号分隔，逐个校验：'+rangeText(ruleOf(table,fd.name));
  });

  $('#clr').onclick = () => { $$('input', f).forEach(i => i.value = ''); $$('.fld', f).forEach(dv => { dv.classList.remove('voice', 'key', 'err'); $('.hint', dv).textContent = $('.hint', dv).dataset.base; }); };
  f.onsubmit = async e => {
    e.preventDefault(); clearErrs(f);
    const body = {}; let bad = false;
    for (const fd of fl) {
      const v = f[fd.name].value.trim();
      if (fd.type !== 'text' && v !== '') {
        const n = v.replace(/[０-９．]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
        if (!/^-?\d+(\.\d+)?$/.test(n)) { setErr(f, fd.name, fd.label + '必须是数字'); bad = true; continue; }
        if (((n.split('.')[1] || '').replace(/0+$/, '')).length > 2) { setErr(f, fd.name, fd.label + '最多两位小数'); bad = true; continue; }
        body[fd.name] = n;
      } else body[fd.name] = v === '' ? null : v;
    }
    if (bad) return;
    const btn = $('button[type=submit]', f); btn.disabled = true;
    try {
      const r = await api('POST', `/contracts/${enc}/records/${kind}`, body);
      showVerdict(r, enc, kind);
    } catch (err) {
      if (err.fields) Object.entries(err.fields).forEach(([k, m]) => setErr(f, k, m));
      else toast(err.message, 'ng');
    }
    btn.disabled = false;
  };
}
function showVerdict(r, enc, kind) {
  const v = r.validation, el = $('#verdict');
  const cs = r.contract_status ? `合同状态：${r.contract_status.status}${r.contract_status.reason ? '（' + r.contract_status.reason + '）' : ''}` : '';
  if (!v) {
    el.innerHTML = `<div class="verdict wait"><div class="big">已保存，校验未完成</div><div style="margin-top:8px;color:var(--tx2)">${esc(r.warning || '')}</div>
      <div class="after"><a class="btn" href="#/rules">去补跑校验</a><a class="btn ghost" href="#/c/${enc}">查看合同</a></div></div>`;
  } else if (v.is_qualified === 1) {
    el.innerHTML = `<div class="verdict ok"><div class="big">✓ 合格</div><div style="margin-top:8px;color:var(--tx2)">记录 #${r.record.id} 已保存 · ${esc(cs)}</div>
      <div class="after"><a class="btn pri" href="#/c/${enc}">查看合同</a>${Object.keys(KINDS).filter(k => k !== kind).map(k => `<a class="btn" href="#/c/${enc}/${k}">录入${KIND_LABEL[k]}</a>`).join('')}</div></div>`;
  } else {
    el.innerHTML = `<div class="verdict ng"><div class="big">✕ 不合格</div>
      <ul>${v.failures.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      <div style="margin-top:12px;color:var(--tx2);font-size:13px">记录 #${r.record.id} 已入库，不会导出。复测后重新录入，最新一条生效 · ${esc(cs)}</div>
      <div class="after"><button class="btn" onclick="window.scrollTo({top:0,behavior:'smooth'});document.querySelector('#f input:not([readonly])').focus()">复测并重新录入</button><a class="btn ghost" href="#/c/${enc}">查看合同</a></div></div>`;
  }
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ================= 语音面板 ================= */
const EXAMPLES = {
  contract: ['合同号二零二六四八二七', '客户样例重工，日期今天', '合同号20260007'],
  drum: ['直径一千二百五十，长度三米', '厚度十二点五毫米，光段三百', '直径1250，不对，1252'],
  control_cabinet: ['绝缘电阻五百兆欧，控制线力矩一点二牛米', '电气间隙八毫米'],
  travel_paint: ['底漆九十八、九十七、九十六', '面漆99%，附着力百分之一'],
  travel_drive: ['噪音八十二分贝', '电机铭牌 YZR160M2-6', '减速机ZQ500，噪声78'],
};
function voicePanelHTML(form) {
  return `<div class="card vp" id="vp">
    <div class="micro">语音填表 · 按住说话</div>
    <button class="mic" id="mic" type="button" aria-label="按住说话">${ICON.mic}</button>
    <div class="tm" id="vtm" hidden>0:00</div>
    <canvas id="wave" width="600" height="84" hidden></canvas>
    <div class="vs" id="vs">按住麦克风说话，松开后识别并填入左侧表单</div>
    <div class="said" id="said" hidden></div>
    <div style="width:100%;border-top:1px solid var(--line);padding-top:14px">
      <div class="micro" style="margin-bottom:8px;text-align:left">没有麦克风？打字模拟识别结果</div>
      <div class="typebox"><input id="vtxt" placeholder="例如：${esc(EXAMPLES[form][0])}"><button class="btn sm" id="vgo" type="button">解析</button></div>
      <div class="examples" style="margin-top:10px">${EXAMPLES[form].map(x => `<button type="button">${esc(x)}</button>`).join('')}</div>
    </div></div>`;
}
function mountVoice(form, f, lockFields) {
  const mic = $('#mic'), vs = $('#vs'), said = $('#said'), tm = $('#vtm'), cv = $('#wave');
  let rec = null, stream = null, chunks = [], t0 = 0, tick = null, raf = null, actx = null, busy = false, cancelled = false;
  const MAX = 60;
  const status = (msg, ng) => { vs.textContent = msg; vs.className = 'vs' + (ng ? ' ng' : ''); };
  const syncAvail = () => {
    if (busy || rec) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { mic.disabled = true; status('当前浏览器不支持录音，请用打字或手动填写', true); return; }
    if (!G.voice.available) { mic.disabled = true; status('语音服务不可用，请手动填写', true); return; }
    mic.disabled = false;
    status(G.voice.loaded === false ? '语音模型还在加载，可以先试，可能稍慢' : '按住麦克风说话，松开后识别并填入左侧表单');
  };
  syncAvail();
  document.addEventListener('voicestatus', syncAvail);

  async function start() {
    if (busy || rec || mic.disabled) return;
    cancelled = false;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true } }); }
    catch (e) { status('没有麦克风权限：请点地址栏左侧的权限图标，允许使用麦克风后重试', true); return; }
    if (cancelled) { stream.getTracks().forEach(t => t.stop()); return; }
    chunks = [];
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find(m => MediaRecorder.isTypeSupported(m)) || '';
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    rec.onstop = onStop;
    rec.start(250);
    mic.classList.add('rec'); tm.hidden = false; cv.hidden = false; status('正在听…松开结束');
    t0 = Date.now();
    tick = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      tm.textContent = `0:${String(s).padStart(2, '0')}`;
      if (s >= MAX) { stop(); toast('已达到最长录音时间', ''); }
    }, 200);
    try {
      actx = new (window.AudioContext || window.webkitAudioContext)();
      const an = actx.createAnalyser(); an.fftSize = 512; actx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.frequencyBinCount), g = cv.getContext('2d');
      const draw = () => {
        an.getByteTimeDomainData(buf); g.clearRect(0, 0, cv.width, cv.height);
        const n = 64, w = cv.width / n;
        for (let i = 0; i < n; i++) {
          const v = Math.abs(buf[Math.floor(i * buf.length / n)] - 128) / 128, h = Math.max(3, v * cv.height * 1.8);
          g.fillStyle = '#ec6a60'; g.fillRect(i * w + 2, (cv.height - h) / 2, w - 4, h);
        }
        raf = requestAnimationFrame(draw);
      };
      draw();
    } catch (e) { /* 波形可选 */ }
  }
  function stop() {
    cancelled = true;
    if (!rec) return;
    clearInterval(tick); cancelAnimationFrame(raf);
    if (rec.state !== 'inactive') rec.stop();
    stream.getTracks().forEach(t => t.stop());
    if (actx) actx.close().catch(() => {});
    mic.classList.remove('rec'); tm.hidden = true; cv.hidden = true;
  }
  async function onStop() {
    const dur = (Date.now() - t0) / 1000, type = rec.mimeType || 'audio/webm';
    rec = null;
    if (dur < 0.5) { status('太短了，请按住麦克风说完再松开'); return; }
    const blob = new Blob(chunks, { type });
    busy = true; mic.classList.add('busy'); mic.disabled = true; status('识别中…');
    const fd = new FormData();
    fd.append('audio', blob, 'voice.' + (type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'));
    fd.append('form', form);
    try { apply(await api('POST', '/voice/parse', fd, true)); }
    catch (e) { status(e.message || '识别失败，请手动填写', true); }
    busy = false; mic.classList.remove('busy'); mic.disabled = !G.voice.available; // 保留识别结果提示，不重置状态文字
  }
  async function typed(text) {
    if (!text.trim()) return;
    status('解析中…');
    try { apply(await api('POST', '/voice/parse-text', { text, form })); }
    catch (e) { status(e.message, true); }
  }
  function apply(r) {
    const names = Object.keys(r.fields || {}).filter(k => !lockFields.includes(k) && f[k]);
    names.forEach(k => {
      const d = $(`.fld[data-f="${k}"]`, f);
      f[k].value = r.fields[k];
      d.classList.remove('err'); d.classList.add('voice'); d.classList.toggle('key', k === 'ht_code');
      $('.hint', d).textContent = r.notes?.[k] ? `${r.notes[k]}，请核对` : (k === 'ht_code' ? '语音填入的合同号，请重点核对' : '语音填入，请核对');
      f[k].dispatchEvent(new Event('input'));
      d.animate([{ transform: 'scale(1.015)' }, { transform: 'none' }], { duration: 260 });
    });
    let txt = esc(r.corrected || r.text || '');
    (r.matched || []).forEach(m => { if (m.keyword) txt = txt.split(esc(m.keyword)).join(`<mark>${esc(m.keyword)}</mark>`); });
    const den = r.asr?.denoise;
    said.hidden = false;
    said.innerHTML = `<div class="micro">识别原文</div><p>${txt || '<span style="color:var(--tx3)">（空）</span>'}</p>
      ${r.corrected && r.text && r.corrected.toLowerCase() !== r.text.normalize('NFKC').toLowerCase() ? `<div class="dn">纠错前：${esc(r.text)}</div>` : ''}
      ${(r.rejected || []).map(x => `<div class="dn" style="color:var(--amber)">「${esc(x.said || x.field)}」没有填入：${esc(x.reason)}</div>`).join('')}
      ${r.asr ? `<div class="dn">${r.asr.duration_s ?? '?'} 秒音频 · 识别 ${r.asr.elapsed_ms ?? '?'} ms · 降噪 ${den ? denoiseText(den) : '—'}</div>` : ''}`;
    if (!names.length) status(r.message || '未识别到参数', true);
    else status(`已填入 ${names.length} 项，请核对后提交`);
  }
  // 按住说话：鼠标 / 触屏
  const down = e => { e.preventDefault(); mic.setPointerCapture?.(e.pointerId); start(); };
  const up = e => { e.preventDefault(); stop(); };
  mic.addEventListener('pointerdown', down);
  mic.addEventListener('pointerup', up);
  mic.addEventListener('pointercancel', up);
  mic.addEventListener('contextmenu', e => e.preventDefault());
  // 按住空格也能说话（焦点不在输入框时）
  const kd = e => { if (e.code === 'Space' && !e.repeat && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); start(); } };
  const ku = e => { if (e.code === 'Space' && rec) { e.preventDefault(); stop(); } };
  window.addEventListener('keydown', kd); window.addEventListener('keyup', ku);
  $('#vgo').onclick = () => typed($('#vtxt').value);
  $('#vtxt').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); typed(e.target.value); } };
  $$('#vp .examples button').forEach(b => b.onclick = () => { $('#vtxt').value = b.textContent; typed(b.textContent); });
  return () => {
    stop(); document.removeEventListener('voicestatus', syncAvail);
    window.removeEventListener('keydown', kd); window.removeEventListener('keyup', ku);
  };
}
function denoiseText(d) {
  if (!d.enabled || d.action === 'off') return '关';
  if (d.action === 'passthrough') return '直通（降噪模块未接入）';
  if (d.action === 'error') return '出错，已用原始音频';
  const m = { bypass: '干净，跳过', light: '轻度', strong: '强' }[d.action] || d.action;
  return `${m}${d.snr_db != null ? ` · 信噪比 ${Number(d.snr_db).toFixed(1)} dB` : ''}`;
}

/* ================= 规则管理 ================= */
async function pageRules() {
  const r = await loadRules();
  app.innerHTML = `
    <div class="ph"><div><h1>规则管理</h1><div class="sub">每个参数的合格范围（含边界）。修改后对新提交的记录立即生效</div></div><span class="sp"></span>
      <button class="btn" id="pend" title="对所有「待校验」的记录执行校验">补跑校验</button></div>
    <div class="prog"><b>${r.configured}<span>/${r.total}</span></b><div><div style="font-size:15px">已配置</div><div style="color:var(--tx3);font-size:12px;margin-top:3px">下限和上限都填了才算配置；未配置的参数一律判为不合格</div></div>
      <div class="segs">${r.items.map(x => `<i class="${x.configured ? 'on' : ''}"></i>`).join('')}</div></div>
    <div class="note amb" style="background:transparent;color:var(--tx2);border-color:var(--line2)">修改规则<b style="margin:0 4px;color:var(--tx)">不会</b>重新判定已有记录，历史结论保持不变；需要时到合同详情点「重新校验」。</div>
    <div class="card"><div id="rows">${r.items.map(ruleRowHTML).join('')}</div></div>`;
  $$('.rrow').forEach(row => {
    const id = row.dataset.id, [mi, ma] = $$('input', row), btn = $('button', row);
    const orig = [mi.value, ma.value];
    const dirty = () => { mi.classList.toggle('dirty', mi.value !== orig[0]); ma.classList.toggle('dirty', ma.value !== orig[1]); btn.disabled = mi.value === orig[0] && ma.value === orig[1]; };
    mi.oninput = ma.oninput = dirty;
    [mi, ma].forEach(i => i.onkeydown = e => { if (e.key === 'Enter' && !btn.disabled) btn.click(); });
    btn.onclick = async () => {
      $('.msg', row)?.remove();
      try {
        await api('PUT', '/rules/' + id, { min: mi.value.trim() || null, max: ma.value.trim() || null });
        toast('规则已保存', 'ok'); pageRules();
      } catch (e) {
        const m = document.createElement('div'); m.className = 'msg'; m.textContent = e.message; row.appendChild(m);
      }
    };
  });
  $('#pend').onclick = async () => {
    try { const x = await api('POST', '/validation/pending'); toast(`补跑完成：处理 ${x.processed} 条，合格 ${x.qualified}，不合格 ${x.unqualified}${x.failed ? `，失败 ${x.failed}` : ''}`, 'ok'); }
    catch (e) { toast(e.message, 'ng'); }
  };
}
function ruleRowHTML(x) {
  return `<div class="rrow" data-id="${x.id}">
    <div class="nm">${esc(x.label)}<small>${esc(x.table_label)} · ${esc(x.name)}</small></div>
    <div>${x.configured ? '<span class="pill ok">已配置</span>' : '<span class="pill wait">未配置</span>'}</div>
    <input inputmode="decimal" placeholder="下限" value="${esc(x.min ?? '')}">
    <span class="til">～</span>
    <input inputmode="decimal" placeholder="上限" value="${esc(x.max ?? '')}">
    <span class="un">${esc(x.unit || '')}</span>
    <button class="btn sm" disabled>保存</button></div>`;
}

/* ================= 导出 ================= */
async function pageExports() {
  app.innerHTML = `
    <div class="ph"><div><h1>导出</h1><div class="sub">一个合同一个 Excel 文件；只导出「合格」的合同，每类参数取最新一条</div></div><span class="sp"></span>
      <button class="btn pri" id="all">${ICON.dl}批量导出全部合格合同</button></div>
    <div id="allres"></div>
    <div class="card"><div class="hd"><h3>已导出的文件</h3><span class="sp"></span><span class="micro">同一天重复导出会覆盖同名文件</span></div><div id="files"><div class="skeleton" style="height:60px"></div></div></div>`;
  const loadFiles = async () => {
    try {
      const r = await api('GET', '/exports');
      $('#files').innerHTML = r.items.length ? `<table class="tbl"><thead><tr><th>文件</th><th>合同号</th><th class="hide-s">导出日期</th><th class="hide-s r">大小</th><th></th></tr></thead><tbody>
        ${r.items.map(x => `<tr><td class="mono" style="font-size:12.5px">${esc(x.rel)}</td><td class="ht">${esc(x.ht_code)}</td><td class="mono hide-s">${esc(x.date)}</td><td class="mono hide-s r" style="color:var(--tx3)">${(x.size / 1024).toFixed(1)} KB</td>
          <td class="r"><a class="btn sm" href="/api/exports/${encodeURIComponent(x.ht_code)}/${esc(x.date.replace(/-/g, ''))}">${ICON.dl}下载</a></td></tr>`).join('')}</tbody></table>`
        : '<div class="empty"><b>还没有导出过文件</b>在合同详情页导出，或点右上角批量导出</div>';
    } catch (e) { $('#files').innerHTML = `<div class="empty"><b>${esc(e.message)}</b></div>`; }
  };
  loadFiles();
  $('#all').onclick = async () => {
    const b = $('#all'); b.disabled = true; b.textContent = '正在导出…';
    try {
      const r = await api('POST', '/export/all');
      $('#allres').innerHTML = `<div class="card" style="margin-bottom:18px"><div class="hd"><h3>批量导出结果</h3><span class="sp"></span>
        <span class="pill ok">成功 ${r.succeeded.length}</span>${r.failed.length ? `<span class="pill ng">失败 ${r.failed.length}</span>` : ''}<span class="pill part">跳过不合格 ${r.skipped}</span></div>
        <div class="res">${r.succeeded.map(x => `<div class="ri"><span class="ht mono">${esc(x.ht_code)}</span><span class="shs">${x.sheets.map(s => `<span class="${s.ok ? '' : 'bad'}" title="${esc(s.error || '')}">${esc(s.name)}${s.ok ? '' : ' · 失败'}</span>`).join('')}</span><span class="sp"></span><span class="mono" style="color:var(--tx3);font-size:12px">${esc(x.file)}</span></div>`).join('')}
        ${r.failed.map(x => `<div class="ri" style="border-color:rgba(236,106,96,.35)"><span class="ht mono">${esc(x.ht_code)}</span><span style="color:var(--red)">${esc(x.reason)}</span></div>`).join('')}
        ${!r.succeeded.length && !r.failed.length ? '<div class="empty" style="padding:20px">没有可导出的合格合同</div>' : ''}</div></div>`;
      loadFiles();
    } catch (e) { toast(e.message, 'ng'); }
    b.disabled = false; b.innerHTML = ICON.dl + '批量导出全部合格合同';
  };
}

/* ---------------- 启动 ---------------- */
refreshVoice();
setInterval(refreshVoice, 15000);
route();

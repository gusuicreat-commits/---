/* 两端共用：接口、实时事件、在线状态、录音、规则预判、回读文案 */
'use strict';
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const KINDS = { drum: 'drum_param', travel_drive: 'travel_drive_param', control_cabinet: 'control_cabinet_param', travel_paint: 'travel_paint_param' };
  const KIND_LABEL = { drum: '卷筒', travel_drive: '大车传动', control_cabinet: '控制柜', travel_paint: '大车油漆', contract: '合同' };
  const STATUS_CLS = { '合格': 'ok', '不合格': 'ng', '待校验': 'warn', '未录全': 'dim' };
  const load = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

  /* ---------- 接口 ---------- */
  class ApiError extends Error { constructor(status, err) { super(err.message || '请求失败'); this.status = status; Object.assign(this, err); } }
  async function api(method, path, body, isForm) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opt.body = body; else { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    }
    let res;
    try { res = await fetch('/api' + path, opt); } catch (e) { throw new ApiError(0, { code: 'NETWORK', message: '连不上服务，请确认主程序已启动' }); }
    if (res.status === 204) return null;
    let data = null; try { data = await res.json(); } catch (e) {}
    if (!res.ok) {
      if (data && data.error) throw new ApiError(res.status, data.error);
      if (res.status === 404) throw new ApiError(404, { code: 'NOT_IMPLEMENTED', message: '该功能接口尚未上线' });
      throw new ApiError(res.status, { code: 'HTTP', message: `服务出错（${res.status}）` });
    }
    return data;
  }

  /* ---------- 公共数据 ---------- */
  const G = { fields: null, rules: null };
  async function loadFields() { if (!G.fields) G.fields = await api('GET', '/fields'); return G.fields; }
  async function loadRules() { G.rules = await api('GET', '/rules'); return G.rules; }
  function fieldsOf(kind) { return G.fields?.[KINDS[kind]]?.fields || []; }
  function fieldMeta(kind, name) { return fieldsOf(kind).find(f => f.name === name); }
  function ruleOf(kind, name) { return (G.rules?.items || []).find(r => r.table_name === KINDS[kind] && r.name === name); }
  const configured = r => r && r.min != null && r.max != null && r.min !== '' && r.max !== '';
  const rangeText = r => configured(r) ? `${r.min}～${r.max}` : '规则未配置';

  /* 按当前规则预判一个字段（只用于提示，最终以后端判定为准） */
  function prejudge(kind, name, value) {
    const f = fieldMeta(kind, name); if (!f) return null;
    if (f.numeric_list) {
      const r = ruleOf(kind, name), parts = String(value ?? '').normalize('NFKC').trim().split(/[,，、;；\s]+/);
      if (!String(value ?? '').trim()) return {cls:'dim', verdict:'未填写', calc:''};
      if (parts.some(v => !/^-?\d+(\.\d{1,2})?$/.test(v))) return {cls:'ng', verdict:'请用逗号分隔数字（最多两位小数）', calc:'', invalid:true};
      if (!configured(r)) return {cls:'warn', verdict:'规则未配置', calc:''};
      const bad = parts.map((v,i)=>({v,i})).filter(x=>Number(x.v)<Number(r.min)||Number(x.v)>Number(r.max));
      const detail = bad.map(x=>`第${x.i+1}个测点 ${x.v} 不合格，超出 ${r.min}～${r.max}`).join('；');
      return {cls:bad.length?'ng':'ok', verdict:detail||'全部测点合格', calc:detail, src:`逐点校验 ${r.min}～${r.max}`};
    }
    if (f.type === 'text') return { cls: 'dim', verdict: '已记录 · 不校验', calc: '', src: `${f.label}仅记录文本，不参与校验` };
    const r = ruleOf(kind, name), v = Number(value);
    const src = configured(r) ? `规则  ${f.label} ${r.min}～${r.max} ${f.unit || ''}` : '规则未配置';
    if (value == null || value === '') return { cls: 'dim', verdict: '未填写', calc: '', src };
    if (!configured(r)) return { cls: 'warn', verdict: '规则未配置', calc: '未配置的参数会被判为不合格', src };
    if (v < Number(r.min)) return { cls: 'ng', verdict: '低于下限 · 提交后记为不合格', calc: `${value} < ${r.min}`, src };
    if (v > Number(r.max)) return { cls: 'ng', verdict: '超出上限 · 提交后记为不合格', calc: `${value} > ${r.max}`, src };
    return { cls: 'ok', verdict: '在范围内', calc: `${r.min} ≤ ${value} ≤ ${r.max}`, src };
  }

  /* 回读文案（屏幕显示 + 可选语音播报） */
  const unitWord = u => ({ mm: '毫米', dB: '分贝', 'MΩ': '兆欧', 'N·m': '牛米', '%': '百分比' }[u] || u || '');
  function readbackVoice(p, kind) {
    const names = Object.keys(p.fields || {});
    if (!names.length) return p.message ? `${p.message}。请再说一次，或者用键盘输入。` : '没有识别到参数。';
    const parts = names.map(n => {
      if (n === 'ht_code') return `合同号 ${p.fields[n]}`;
      if (n === 'name') return `客户 ${p.fields[n]}`;
      if (n === 'date') return `日期 ${p.fields[n]}`;
      const f = fieldMeta(kind, n); if (!f) return '';
      if (f.type === 'text' && !f.numeric_list) return `${f.label} ${p.fields[n]}`;
      const j = prejudge(kind, n, p.fields[n]);
      const note = p.notes?.[n] ? `（${p.notes[n]}）` : '';
      return `${f.label} ${f.unit === '%' ? '百分之' : ''}${p.fields[n]} ${f.unit === '%' ? '' : unitWord(f.unit)}${note}${j && j.cls === 'ng' ? `，超出 ${rangeText(ruleOf(kind, n))}` : ''}`;
    }).filter(Boolean);
    const rej = (p.rejected || []).map(x => `「${x.said || x.field}」${x.reason}`);
    return parts.join('，') + '。' + (rej.length ? `另外 ${rej.join('，')}。` : '') + '不对请直接说更正。';
  }
  function readbackRecord(r, kind) {
    const v = r.validation;
    if (!v) return `已保存，校验未完成，${r.warning || '可稍后补跑校验'}。`;
    if (v.is_qualified === 1) return `${KIND_LABEL[kind]}参数合格，已保存。合同状态：${r.contract_status?.status || ''}。`;
    return `已保存，${KIND_LABEL[kind]}参数记为不合格：${v.failures.join('；')}。需要复测时重新录入即可。`;
  }

  /* ---------- 实时事件 ---------- */
  const bus = { subs: [], last: 0, boot: null, es: null, mode: 'off', onState: null };
  function onEvent(fn) { bus.subs.push(fn); }
  function emit(ev) { if (ev.id <= bus.last) return; bus.last = ev.id; bus.subs.forEach(f => { try { f(ev); } catch (e) { console.error(e); } }); }
  function setMode(m) { bus.mode = m; bus.onState && bus.onState(m); }
  async function connect(opts = {}) {
    // 先取当前最新 id（默认不重放历史，replay=n 时重放最近 n 条）
    try {
      const r = await api('GET', '/events?after=0&limit=500');
      bus.boot = r.boot;
      const items = opts.replay ? r.items.slice(-opts.replay) : [];
      bus.last = opts.replay ? (items[0]?.id || r.last_id + 1) - 1 : r.last_id;
      items.forEach(emit);
    } catch (e) { setMode('none'); return; }
    if (window.EventSource) {
      const open = () => {
        const es = new EventSource('/api/events/stream?after=' + bus.last); bus.es = es;
        es.addEventListener('hello', e => { const h = JSON.parse(e.data); if (bus.boot && h.boot !== bus.boot) { bus.boot = h.boot; bus.last = 0; } setMode('live'); });
        es.onmessage = e => { try { emit(JSON.parse(e.data)); } catch (x) {} };
        es.onerror = () => { setMode('retry'); es.close(); setTimeout(open, 2000); };
      };
      open();
    } else {
      setMode('poll');
      setInterval(async () => { try { const r = await api('GET', '/events?after=' + bus.last); r.items.forEach(emit); } catch (e) {} }, 1500);
    }
  }
  function publish(type, data, source) { return api('POST', '/events', { type, source, data }).catch(() => null); }

  /* ---------- 在线状态 ---------- */
  const clientId = load('qc_client', null) || (() => { const id = Math.random().toString(36).slice(2, 10); store('qc_client', id); return id; })();
  function presence(role, onPeers) {
    const tick = async () => {
      try { await api('POST', '/presence', { role, client: clientId }); if (onPeers) onPeers(await api('GET', '/presence')); }
      catch (e) { if (onPeers) onPeers(null); }
    };
    tick(); return setInterval(tick, 4000);
  }

  /* ---------- 语音服务状态 ---------- */
  async function voiceStatus() { try { return await api('GET', '/voice/status'); } catch (e) { return { available: false }; } }

  /* ---------- 录音（按住说话） ---------- */
  function micSupport() {
    if (!window.isSecureContext) return { ok: false, why: '手机浏览器只在 https 下允许麦克风，请用 tools/run_https.sh 启动，或用键盘输入' };
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return { ok: false, why: '当前浏览器不支持录音，请用键盘输入' };
    return { ok: true };
  }
  function recorder({ onLevel, onStart, maxSec = 60, onMax } = {}) {
    let rec = null, stream = null, chunks = [], t0 = 0, actx = null, raf = null, timer = null, cancelled = false;
    async function start() {
      if (rec) return false; cancelled = false;
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true } });
      if (cancelled) { stream.getTracks().forEach(t => t.stop()); return false; }
      chunks = [];
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find(m => MediaRecorder.isTypeSupported(m)) || '';
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      rec.ondataavailable = e => e.data.size && chunks.push(e.data);
      rec.start(250); t0 = Date.now(); onStart && onStart();
      timer = setInterval(() => { if ((Date.now() - t0) / 1000 >= maxSec) { onMax && onMax(); stop(); } }, 250);
      try {
        actx = new (window.AudioContext || window.webkitAudioContext)();
        const an = actx.createAnalyser(); an.fftSize = 512; actx.createMediaStreamSource(stream).connect(an);
        const buf = new Uint8Array(an.frequencyBinCount);
        const loop = () => { an.getByteTimeDomainData(buf); let m = 0; for (const b of buf) m = Math.max(m, Math.abs(b - 128)); onLevel && onLevel(Math.min(1, m / 64)); raf = requestAnimationFrame(loop); };
        loop();
      } catch (e) {}
      return true;
    }
    function stop() {
      cancelled = true;
      return new Promise(res => {
        if (!rec) return res(null);
        clearInterval(timer); cancelAnimationFrame(raf); onLevel && onLevel(0);
        const r = rec, dur = (Date.now() - t0) / 1000; rec = null;
        r.onstop = () => { stream.getTracks().forEach(t => t.stop()); if (actx) actx.close().catch(() => {}); res({ blob: new Blob(chunks, { type: r.mimeType || 'audio/webm' }), dur }); };
        if (r.state !== 'inactive') r.stop(); else r.onstop();
      });
    }
    return { start, stop, get on() { return !!rec; } };
  }
  async function sendAudio(blob, form, ht_code, source) {
    const type = blob.type || 'audio/webm';
    const fd = new FormData();
    fd.append('audio', blob, 'voice.' + (type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'));
    fd.append('form', form); if (ht_code) fd.append('ht_code', ht_code); if (source) fd.append('source', source);
    return api('POST', '/voice/parse', fd, true);
  }
  const sendText = (text, form, ht_code, source) => api('POST', '/voice/parse-text', { text, form, ht_code: ht_code || undefined, source });

  /* ---------- 语音播报 ---------- */
  function speak(text) {
    if (!('speechSynthesis' in window)) return;
    try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text.replace(/～/g, '到').replace(/dB/g, '分贝').replace(/mm/g, '毫米')); u.lang = 'zh-CN'; u.rate = 1.08; speechSynthesis.speak(u); } catch (e) {}
  }

  function denoiseText(d) {
    if (!d) return '—';
    if (!d.enabled || d.action === 'off') return '关';
    if (d.action === 'passthrough') return '直通（降噪模块未接入）';
    if (d.action === 'error') return '出错，已用原始音频';
    const m = { bypass: '干净，跳过', light: '轻度', strong: '强' }[d.action] || d.action;
    return `${m}${d.snr_db != null ? ` · 信噪比 ${Number(d.snr_db).toFixed(1)} dB` : ''}`;
  }
  const nowBJ = () => new Date(Date.now() + 8 * 3600e3);
  const hhmmss = () => nowBJ().toISOString().slice(11, 19);
  const todayStr = () => nowBJ().toISOString().slice(0, 10);

  window.QC = { esc, KINDS, KIND_LABEL, STATUS_CLS, load, store, api, ApiError, G, loadFields, loadRules, fieldsOf, fieldMeta, ruleOf, rangeText, configured,
    prejudge, readbackVoice, readbackRecord, bus, onEvent, connect, publish, presence, clientId, voiceStatus, micSupport, recorder, sendAudio, sendText,
    speak, denoiseText, hhmmss, todayStr, unitWord };
})();

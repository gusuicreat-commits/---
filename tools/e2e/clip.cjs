// 真实录音用作演示：手机录一段 → 大屏证据追溯设为演示第 3 步 → 一键演示时先播录音再解析
// 用法：QC_BASE=http://localhost:8602/ node tools/e2e/clip.cjs <输出目录>
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const B = process.env.QC_BASE || 'http://localhost:8600/'; const ORIGIN = new URL(B).origin; const OUT = process.argv[2] || '.'; const A = __dirname + '/audio/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const br = await chromium.launch({ channel: 'chrome', args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${A}a1.wav%noloop`, '--autoplay-policy=no-user-gesture-required'] });
  const sc = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 } });
  const ph = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 470, height: 960 } });
  await ph.grantPermissions(['microphone'], { origin: ORIGIN });
  const rq = sc.request; const errs = [];
  const used = new Set((await (await rq.get(B + 'api/contracts')).json()).items.map(c => c.ht_code));
  const day = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10).replace(/-/g, '');
  const HT = [10, 20, 30, 40, 50, 60, 70, 80, 90].map(n => `${day}-${n}`).find(c => !used.has(c));
  await rq.post(B + 'api/contracts', { data: { ht_code: HT, name: '录音样本（样例）' } });
  const S = await sc.newPage(), P = await ph.newPage();
  [S, P].forEach((p, i) => p.on('pageerror', e => errs.push((i ? '手机 ' : '大屏 ') + e.message)));
  await S.goto(B + 'screen.html'); await sleep(1200);
  await P.addInitScript(ht => { localStorage.setItem('qcp_ht', JSON.stringify(ht)); localStorage.setItem('qcp_form', '"drum"'); localStorage.removeItem('qcp_draft'); }, HT);
  await P.goto(B + 'phone.html'); await sleep(2500);
  const box = await (await P.$('#mic')).boundingBox();
  await P.mouse.move(box.x + 33, box.y + 33); await P.mouse.down(); await sleep(4800); await P.mouse.up();
  await P.waitForFunction(() => /直径/.test(document.getElementById('q').textContent), null, { timeout: 20000 });
  console.log('手机录音:', await P.textContent('#q'));
  // 大屏证据追溯 → 设为第 3 步
  await S.click('.tabs button[data-v="evi"], nav [data-v="evi"]').catch(() => S.click('text=证据追溯')); await sleep(1200);
  await S.click('#evList >> text=直径一千二百五十'); await sleep(1200);
  await S.selectOption('#clipStep', '2'); await S.screenshot({ path: OUT + '/c1_证据_设为演示.png' });
  await S.click('#clipGo'); await sleep(1500);
  const sc1 = await (await rq.get(B + 'api/demo/script')).json();
  console.log('第 3 步录音:', JSON.stringify(sc1.steps[2].clip && { text: sc1.steps[2].clip.text, dur: sc1.steps[2].clip.duration_s }));
  await S.click('text=识别纠错'); await sleep(1200); await S.$eval('#clipPanel', e => e.scrollIntoView()); await sleep(300);
  await S.screenshot({ path: OUT + '/c2_识别纠错_演示录音表.png' });
  console.log('录音表:', (await S.textContent('#clipRows')).replace(/\s+/g, ' ').slice(0, 160));
  // 一键演示
  await S.evaluate(() => { window.__played = []; const o = Audio.prototype.play; Audio.prototype.play = function () { window.__played.push(this.src); return o.call(this); }; });
  await S.click('#dmBtn'); let shot = false;
  for (let i = 0; i < 120; i++) {
    await sleep(500);
    const cap = await S.textContent('#cap');
    if (!shot && /正在播放现场真实录音/.test(cap)) { shot = true; console.log('播放中字幕:', cap.trim()); await S.screenshot({ path: OUT + '/c3_演示_播放真实录音.png' }); await P.screenshot({ path: OUT + '/c3_手机_播放真实录音.png' }); }
    if (shot && /直径一千二百五十/.test(await S.textContent('#tr'))) { await sleep(1500); await S.screenshot({ path: OUT + '/c4_演示_录音识别结果.png' }); console.log('识别后大屏:', (await S.textContent('#tr')).trim(), '|', (await S.textContent('#hero')).replace(/\s+/g, ' ').slice(0, 60)); break; }
  }
  console.log('播放过的音频:', await S.evaluate(() => window.__played));
  await S.waitForFunction(() => /一键演示/.test(document.getElementById('dmBtn').textContent), null, { timeout: 90000 });
  console.log('演示结束，闸门:', await S.textContent('#gateP'));
  // 清理演示合同后录音仍在
  await rq.post(B + 'api/demo/reset'); const au = await rq.get(B + 'api/demo/clips/2/audio');
  console.log('清理演示后录音仍可播放:', au.status(), (await au.body()).length, '字节');
  console.log('页面错误:', errs.length ? errs : '无');
  await br.close();
})().catch(e => { console.error(e); process.exit(1); });

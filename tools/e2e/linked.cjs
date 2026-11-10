// 大屏 + 手机端联动全流程（真实后端 + 真实 FunASR + 假麦克风）
// 覆盖：手机表单直接填、语音填表、提交判定、大屏手动录入抽屉（含新建合同）、派单
// 用法：node tools/e2e/linked.cjs <输出目录>
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const B = process.env.QC_BASE || 'http://localhost:8600/'; const ORIGIN = new URL(B).origin; const OUT = process.argv[2] || '.'; const A = __dirname + '/audio/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const br = await chromium.launch({ channel: 'chrome', args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${A}a1.wav%noloop`] });
  const sc = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 } });
  const ph = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 470, height: 960 } });
  await ph.grantPermissions(['microphone'], { origin: ORIGIN });
  const rq = sc.request;
  const used = new Set((await (await rq.get(B + 'api/contracts')).json()).items.map(c => c.ht_code));
  const day = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10).replace(/-/g, '');
  const free = [10, 20, 30, 40, 50, 60, 70, 80, 90].map(n => `${day}-${n}`).filter(c => !used.has(c));
  const HT = free[0], HT2 = free[1];
  if (!HT) throw new Error('今天合同号已用完，先在大屏「合同台账」清理演示合同');
  console.log('建合同', HT, (await rq.post(B + 'api/contracts', { data: { ht_code: HT, name: '联动演示客户（样例）' } })).status());
  const S = await sc.newPage(), P = await ph.newPage(); const errs = [];
  [S, P].forEach((p, i) => { p.on('pageerror', e => errs.push((i ? '手机 ' : '大屏 ') + e.message)); p.on('console', m => m.type() === 'error' && errs.push((i ? '手机 ' : '大屏 ') + m.text())); });
  await S.goto(B + 'index.html'); await sleep(1200); await S.screenshot({ path: OUT + '/l0_启动页.png' });
  await S.goto(B + 'screen.html'); await sleep(1500);
  await P.addInitScript(ht => { localStorage.setItem('qcp_ht', JSON.stringify(ht)); localStorage.setItem('qcp_form', '"drum"'); localStorage.removeItem('qcp_draft'); localStorage.removeItem('qcp_vids'); }, HT);
  await P.goto(B + 'phone.html'); await sleep(2500);
  console.log('大屏跟随手机合同:', await S.$eval('#ctSel', e => e.value), '|', await S.textContent('#devPhone'));
  await P.screenshot({ path: OUT + '/l1_手机_表单.png' });

  // 1) 手机直接在表里填：直径超上限
  await P.fill('input[data-f="diameter"]', '1262'); await sleep(1200);
  console.log('手机直径行:', await P.$eval('.fr[data-f="diameter"]', e => e.className + ' ' + e.querySelector('.tip').textContent), '| 大屏草稿:', await S.$eval('.row[data-f="diameter"] .val', e => e.textContent));
  await P.screenshot({ path: OUT + '/l2_手机_手填超范围.png' }); await S.screenshot({ path: OUT + '/l2_大屏_同步草稿.png' });

  // 2) 按住说话（a1：直径一千二百五十，光段三百）
  const box = await P.$('#mic').then(e => e.boundingBox());
  await P.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await P.mouse.down(); await sleep(1500);
  await P.screenshot({ path: OUT + '/l3_手机_录音中.png' });
  await sleep(3300); await P.mouse.up();
  await P.waitForFunction(() => !document.getElementById('mic').classList.contains('busy') && /直径/.test(document.getElementById('q').textContent), null, { timeout: 20000 }); await sleep(1500);
  console.log('识别:', await P.textContent('#q'), '| 填入:', await P.$eval('input[data-f="diameter"]', e => e.value), await P.$eval('input[data-f="light"]', e => e.value));
  await P.screenshot({ path: OUT + '/l3_手机_语音填表.png' }); await S.screenshot({ path: OUT + '/l3_大屏_语音识别.png' });

  // 3) 手填长度、厚度 → 提交
  await P.fill('input[data-f="length"]', '3000'); await P.fill('input[data-f="thick"]', '20'); await sleep(400);
  await P.click('#sub'); await sleep(2200);
  console.log('提交后 手机:', (await P.textContent('#banner')).trim(), '| 大屏:', await S.textContent('#hero .h-val'));
  await P.screenshot({ path: OUT + '/l4_手机_卷筒合格.png' }); await S.screenshot({ path: OUT + '/l4_大屏_卷筒合格.png' });

  // 4) 大屏「＋ 录入」抽屉：大车传动，噪声超标
  await S.click('#addBtn'); await sleep(600);
  await S.click('#fKind button[data-k="travel_drive"]'); await sleep(300);
  await S.fill('#fv_reducer_nameplate', 'ZQ500 样例'); await S.fill('#fv_motor_nameplate', 'YZR160M2-6 样例'); await S.fill('#fv_noise', '91'); await sleep(300);
  await S.screenshot({ path: OUT + '/l5_大屏_录入抽屉.png' });
  await S.click('#fGo'); await sleep(2200);
  console.log('抽屉提交:', (await S.textContent('#fRes')).trim(), '| 手机:', (await P.textContent('#banner')).trim());
  await S.screenshot({ path: OUT + '/l5_大屏_抽屉结果.png' }); await P.screenshot({ path: OUT + '/l5_手机_收到大屏提交.png' });
  // 复测：改噪声 82
  await S.fill('#fv_noise', '82'); await S.click('#fGo'); await sleep(2000);
  console.log('复测后:', (await S.textContent('#fRes')).trim(), '|', await S.textContent('#gateP'));
  await S.click('#dwX'); await sleep(500);

  // 5) 抽屉里新建合同并直接录入
  await S.click('#addBtn'); await sleep(600); await S.selectOption('#fCt', '__new__'); await sleep(800);
  if (HT2) await S.fill('#nHt', HT2);
  const nht = await S.$eval('#nHt', e => e.value);
  if (nht) {
    await S.fill('#nName', '抽屉新建客户（样例）'); await S.click('#fKind button[data-k="drum"]'); await sleep(300);
    await S.fill('#fv_diameter', '1250'); await S.fill('#fv_length', '3000'); await S.fill('#fv_thick', '20'); await S.fill('#fv_light', '310');
    await S.click('#fGo'); await sleep(2500);
    console.log('新建合同', nht, ':', (await S.textContent('#fRes')).trim(), '| 手机跟随到:', await P.textContent('#ctH'));
    await S.screenshot({ path: OUT + '/l6_大屏_新建并录入.png' }); await P.screenshot({ path: OUT + '/l6_手机_被切换.png' });
    await S.click('#dwX'); await sleep(400);
  } else { console.log('今天合同号已用完，跳过抽屉新建'); await S.click('#dwX'); await sleep(400); }

  // 6) 派单 → 手机切换
  const btn = await S.$('#cards .cx.ng:not(.cur) button.p');
  if (btn) { const tgt = await btn.evaluate(b => b.dataset.ht); await btn.click(); await sleep(2500); console.log('派单', tgt, '→ 手机:', await P.textContent('#ctH')); await P.screenshot({ path: OUT + '/l7_手机_收到派单.png' }); }
  await P.click('#ctBtn'); await sleep(800); await P.screenshot({ path: OUT + '/l8_手机_合同列表.png' });
  console.log('页面错误:', errs.length ? errs : '无');
  await br.close();
})().catch(e => { console.error(e); process.exit(1); });

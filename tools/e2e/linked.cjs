// 大屏 + 手机端联动全流程（真实后端 + 真实 FunASR + 假麦克风）
// 用法：node tools/e2e/linked.cjs <输出目录>
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const B = 'http://localhost:8600/'; const OUT = process.argv[2] || '.'; const A = __dirname + '/audio/';
let HT;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const br = await chromium.launch({ channel: 'chrome', args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${A}a1.wav%noloop`, '--autoplay-policy=no-user-gesture-required'] });
  const api = await br.newContext(); const rq = api.request;
  HT = (await (await rq.get(B + 'api/contracts/suggest')).json()).ht_code;
  const cr = await rq.post(B + 'api/contracts', { data: { ht_code: HT, name: '联动演示客户（样例）' } });
  console.log('建合同', cr.status());
  const sc = await br.newContext({ viewport: { width: 1600, height: 900 } });
  const ph = await br.newContext({ viewport: { width: 470, height: 960 } });
  await ph.grantPermissions(['microphone'], { origin: 'http://localhost:8600' });
  const S = await sc.newPage(), P = await ph.newPage();
  const errs = []; [S, P].forEach((p, i) => { p.on('pageerror', e => errs.push((i ? '手机 ' : '大屏 ') + e.message)); p.on('console', m => m.type() === 'error' && errs.push((i ? '手机 ' : '大屏 ') + m.text())); });
  await S.goto(B + 'index.html'); await sleep(1200); await S.screenshot({ path: OUT + '/l0_启动页.png' });
  await S.goto(B + 'screen.html'); await sleep(1500);
  await P.addInitScript(ht => { localStorage.setItem('qcp_ht', JSON.stringify(ht)); localStorage.setItem('qcp_form', '"drum"'); localStorage.removeItem('qcp_draft'); }, HT);
  await P.goto(B + 'phone.html'); await sleep(2500);
  console.log('大屏跟随手机合同:', await S.$eval('#ctSel', e => e.value), '| 手机在线:', await S.textContent('#devPhone'));
  await S.screenshot({ path: OUT + '/l1_大屏_跟随手机.png' }); await P.screenshot({ path: OUT + '/l1_手机_选好合同.png' });

  // 1) 键盘说一句：直径超上限 + 长度
  await P.click('#kb'); await sleep(400); await P.fill('#ta', '直径一千二百六十二，长度三千'); await P.click('#send'); await sleep(2200);
  console.log('手机灵动岛:', await P.textContent('#islT'), '| 大屏判定:', await S.textContent('#hero .h-verdict'));
  await S.screenshot({ path: OUT + '/l2_大屏_超范围.png' }); await P.screenshot({ path: OUT + '/l2_手机_超范围.png' });

  // 2) 按住麦克风说（a1：直径一千二百五十，光段三百）
  const box = await P.$('#mic').then(e => e.boundingBox());
  await P.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await P.mouse.down(); await sleep(1500);
  await P.screenshot({ path: OUT + '/l3_手机_录音中.png' });
  await sleep(3200); await P.mouse.up();
  await P.waitForFunction(() => !document.getElementById('mic').classList.contains('busy') && /直径/.test(document.getElementById('live').textContent), null, { timeout: 20000 });
  await sleep(1800);
  console.log('识别:', await P.textContent('#live'), '| 大屏管线:', await S.$$eval('#pipe li small', l => l.map(x => x.textContent).join(' / ')));
  await S.screenshot({ path: OUT + '/l3_大屏_语音识别.png' }); await P.screenshot({ path: OUT + '/l3_手机_识别结果.png' });

  // 3) 补厚度 → 提交
  await P.click('#kb'); await sleep(400); await P.fill('#ta', '厚度二十'); await P.click('#send'); await sleep(1500);
  await P.click('#sub'); await sleep(2200);
  console.log('提交后 手机:', await P.textContent('#hero .v'), '| 大屏:', await S.textContent('#hero .h-val'), await S.textContent('#gateP'));
  await S.screenshot({ path: OUT + '/l4_大屏_卷筒合格.png' }); await P.screenshot({ path: OUT + '/l4_手机_卷筒合格.png' });

  // 4) 大车传动：噪声超标 → 提交不合格
  await P.click('#tabs button[data-k="travel_drive"]'); await sleep(600);
  await P.click('#kb'); await sleep(400); await P.fill('#ta', '减速机铭牌 ZQ500 样例，电机铭牌 YZR160M2-6 样例，噪声九十一'); await P.click('#send'); await sleep(1500);
  await P.click('#sub'); await sleep(2200);
  console.log('大车传动 大屏:', await S.textContent('#hero .h-val'), '|', await S.textContent('#gateP'));
  await S.screenshot({ path: OUT + '/l5_大屏_大车不合格.png' }); await P.screenshot({ path: OUT + '/l5_手机_大车不合格.png' });

  // 5) 大屏派单给手机复测（派另一个不合格合同）
  const btn = await S.$('#cards .cx.ng:not(.cur) button.p');
  const tgt = await btn.evaluate(b => b.dataset.ht); await btn.click(); await sleep(2500);
  console.log('派单', tgt, '→ 手机合同:', await P.textContent('#ctH'), '| 岛:', await P.textContent('#islT'));
  await S.screenshot({ path: OUT + '/l6_大屏_派单.png' }); await P.screenshot({ path: OUT + '/l6_手机_收到派单.png' });

  // 6) 大屏代录 + 其他视图
  await S.fill('#mInput', '光段三百一十'); await S.press('#mInput', 'Enter'); await sleep(2200);
  console.log('代录后手机草稿:', await P.$$eval('#draft button.fresh .val, #draft button .val', l => l.map(x => x.textContent).join(' | ')));
  for (const [v, n] of [['asr', 'l7_大屏_识别纠错'], ['led', 'l8_大屏_合同台账'], ['ver', 'l9_大屏_验证看板']]) { await S.click(`#nav button[data-v="${v}"]`); await sleep(v === 'ver' ? 2500 : 1200); await S.screenshot({ path: `${OUT}/${n}.png` }); }
  await P.click('#ctBtn'); await sleep(800); await P.screenshot({ path: OUT + '/l10_手机_合同列表.png' });
  console.log('页面错误:', errs.length ? errs : '无');
  await br.close();
})().catch(e => { console.error(e); process.exit(1); });

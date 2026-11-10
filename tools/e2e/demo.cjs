// 一键演示 + 复核学习纠错 + 证据追溯（真实后端 + 真实 FunASR + 假麦克风）
// 用法：node tools/e2e/demo.cjs <输出目录>
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const B = process.env.QC_BASE || 'http://localhost:8600/'; const ORIGIN = new URL(B).origin; const OUT = process.argv[2] || '.'; const A = __dirname + '/audio/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const br = await chromium.launch({ channel: 'chrome', args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${A}a1.wav%noloop`, '--autoplay-policy=no-user-gesture-required'] });
  const sc = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 } }), ph = await br.newContext({ ignoreHTTPSErrors: true, viewport: { width: 470, height: 960 } });
  await ph.grantPermissions(['microphone'], { origin: ORIGIN });
  const S = await sc.newPage(), P = await ph.newPage(); const errs = [];
  [S, P].forEach((p, i) => { p.on('pageerror', e => errs.push((i ? '手机 ' : '大屏 ') + e.message)); p.on('console', m => m.type() === 'error' && errs.push((i ? '手机 ' : '大屏 ') + m.text())); });
  // 清理上次学到的「光蛋」，保证可重复运行
  const rq = sc.request; const lc = await (await rq.get(B + 'api/corrections')).json();
  for (const c of lc.learned.filter(x => x.wrong === '光蛋')) await rq.delete(B + 'api/corrections/' + c.id);
  await S.goto(B + 'screen.html'); await P.goto(B + 'phone.html'); await sleep(2500);

  // 1) 一键演示（2 倍速）
  await S.selectOption('#dmSpd', '2'); await S.click('#dmBtn');
  const shots = { 2: '演示_超上限', 3: '演示_子径口音', 4: '演示_口音纠错', 5: '演示_转复核', 8: '演示_噪声超标', 9: '演示_大车不合格', 10: '演示_得劲复测' };
  const seen = new Set();
  for (let t = 0; t < 120; t++) {
    const cap = await S.textContent('#cap').catch(() => '');
    const m = cap.match(/演示 (\d+)\/(\d+)/);
    if (m && !seen.has(+m[1])) { seen.add(+m[1]); if (shots[+m[1]]) { await sleep(900); await S.screenshot({ path: `${OUT}/d${m[1]}_大屏_${shots[+m[1]]}.png` }); await P.screenshot({ path: `${OUT}/d${m[1]}_手机_${shots[+m[1]]}.png` }); console.log('第', m[1], '步', cap.replace(/演示 \d+\/\d+/, '').trim(), '| 大屏:', (await S.textContent('#hero .h-verdict')).trim()); } }
    if (seen.size && !m && await S.$eval('#cap', e => e.hidden)) break;
    await sleep(300);
  }
  await sleep(1500);
  console.log('演示结束 导出闸门:', await S.textContent('#gateP'), '| 合同:', await S.textContent('#detailContract'));
  await S.screenshot({ path: `${OUT}/d12_大屏_演示完成.png` }); await P.screenshot({ path: `${OUT}/d12_手机_演示完成.png` });

  // 2) 手机换到另一个（非演示）合同再录音，避免演示合同因混入真人语音而不能回收
  const other = (await (await sc.request.get(B + 'api/contracts')).json()).items.find(c => c.name !== '演示客户（样例）' && c.status === '未录全');
  await P.click('#ctBtn'); await sleep(900); await P.click(`#ctList .ci[data-ht="${other.ht_code}"]`); await sleep(1500);
  console.log('手机换到合同', other.ht_code);
  await P.click('#tabs button[data-k="drum"]'); await sleep(600);
  const box = await P.$('#mic').then(e => e.boundingBox());
  await P.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await P.mouse.down(); await sleep(4700); await P.mouse.up();
  await P.waitForFunction(() => /直径/.test(document.getElementById('q').textContent), null, { timeout: 20000 }); await sleep(1500);
  console.log('手机录音识别:', await P.textContent('#q'));

  // 3) 听不懂的口音 → 复核 → 学进纠错表
  await S.fill('#mInput', '光蛋三百一十'); await S.press('#mInput', 'Enter'); await sleep(2000);
  await S.click('#nav button[data-v="asr"]'); await sleep(1500);
  const card = (await S.$$('#rvCards .cx[data-rv]')).find ? await (async () => { for (const c of await S.$$('#rvCards .cx[data-rv]')) if ((await c.textContent()).includes('光蛋')) return c; })() : null;
  console.log('待复核:', (await card.textContent()).replace(/\s+/g, ' ').slice(0, 60));
  await card.$eval('[data-w]', e => e.value = '光蛋'); await (await card.$('.q button[data-l="光段"]')).click();
  await S.screenshot({ path: `${OUT}/d13_大屏_复核.png` });
  await (await card.$('button[data-act="learn"]')).click(); await sleep(1800);
  await S.screenshot({ path: `${OUT}/d14_大屏_学进纠错表.png` });
  await S.fill('#mInput', '光蛋三百零五'); await S.press('#mInput', 'Enter'); await sleep(2000);
  console.log('学习后再说「光蛋三百零五」→ 手机光段:', await P.$eval('input[data-f="light"]', e => e.value), '| 大屏:', (await S.textContent('#asrRows tr:first-child')).replace(/\s+/g, ' '));

  // 4) 证据追溯：选有录音的那条并回放
  await S.click('#nav button[data-v="evi"]'); await sleep(1200);
  await S.click('#evF button:nth-child(3)'); await sleep(1200);
  await S.click('#evList .ev-i'); await sleep(1500);
  await S.click('#evPlay'); await sleep(2000);
  console.log('录音回放进度条已走:', await S.evaluate(() => evPlayT > 0));
  await S.screenshot({ path: `${OUT}/d15_大屏_证据回放.png` });
  await S.click('#evF button:nth-child(1)'); await sleep(1000);
  await S.click('#evList .ev-i:nth-child(1)'); await sleep(1500);
  await S.screenshot({ path: `${OUT}/d16_大屏_证据时间线.png` });
  console.log('页面错误:', errs.length ? errs : '无');
  await br.close();
})().catch(e => { console.error(e); process.exit(1); });

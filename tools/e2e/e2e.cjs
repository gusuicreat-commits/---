// 完整流程：真实主程序 + 真实 FunASR + Chrome 假麦克风播放录音
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const fs = require('fs'); const B = 'http://localhost:8600/'; const SP = process.argv[2]; const A = SP + '/audio/';
const errs = []; const log = (...a) => console.log(...a);
async function browser(wav) {
  const args = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'];
  if (wav) args.push('--use-file-for-fake-audio-capture=' + A + wav + '%noloop');
  const b = await chromium.launch({ channel: 'chrome', args });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  await ctx.grantPermissions(['microphone'], { origin: 'http://localhost:8600' });
  const p = await ctx.newPage();
  p.on('pageerror', e => errs.push(e.message));
  return { b, p };
}
async function speak(p, wav, secs) {          // 按住麦克风 secs 秒后松开，等识别结果
  await p.waitForFunction(() => !document.querySelector('#mic').disabled, null, { timeout: 60000 });
  const box = await p.$('#mic').then(e => e.boundingBox());
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.mouse.down(); await p.waitForTimeout(secs * 1000); await p.mouse.up();
  await p.waitForFunction(() => /已填入|未识别|失败|不可用/.test(document.querySelector('#vs').textContent), null, { timeout: 90000 });
  const said = await p.$eval('#said', e => e.innerText).catch(() => '');
  log(`  [${wav}] ${await p.$eval('#vs', e => e.textContent)} | ${said.replace(/\n+/g, ' / ')}`);
}
const vals = p => p.$$eval('#f input', a => Object.fromEntries(a.map(i => [i.name, i.value])));
(async () => {
  let { b, p } = await browser();
  // 1. 配规则（规则页 UI）
  await p.goto(B + '#/rules'); await p.waitForTimeout(800);
  const R = [['1240', '1260'], ['2990', '3010'], ['18', '22'], ['300', '320'], ['0', '85']];
  for (let i = 0; i < 5; i++) {
    const row = (await p.$$('.rrow'))[i], ins = await row.$$('input');
    await ins[0].fill(R[i][0]); await ins[1].fill(R[i][1]); await (await row.$('button')).click(); await p.waitForTimeout(500);
  }
  log('1 规则', await p.$eval('.prog b', e => e.innerText.replace(/\s/g, '')));
  await p.screenshot({ path: SP + '/e_rules.png' }); await b.close();

  // 2. 新建合同：语音说合同号
  ({ b, p } = await browser('a4.wav'));
  await p.goto(B + '#/new'); await p.waitForTimeout(1500);
  await speak(p, 'a4.wav', 4.5);
  await p.fill('input[name=name]', '端到端测试客户');
  log('2 合同表单', JSON.stringify(await vals(p)));
  await p.screenshot({ path: SP + '/e_new.png' });
  await p.click('button[type=submit]'); await p.waitForTimeout(1000);
  const ht = decodeURIComponent((p.url().split('#/c/')[1] || ''));
  log('  创建后 →', ht); await b.close();

  // 3. 卷筒：语音说「直径一千二百五十，光段三百」，再语音「厚度十二点五毫米，长度三千」
  ({ b, p } = await browser('a1.wav'));
  await p.goto(B + '#/c/' + encodeURIComponent(ht) + '/drum'); await p.waitForTimeout(1500);
  await speak(p, 'a1.wav', 4.5); const v1 = await vals(p); await b.close();
  ({ b, p } = await browser('a2.wav'));
  await p.goto(B + '#/c/' + encodeURIComponent(ht) + '/drum'); await p.waitForTimeout(1500);
  for (const [k, v] of Object.entries(v1)) if (v) await p.fill(`input[name=${k}]`, v);
  await speak(p, 'a2.wav', 4.5);
  log('3 卷筒表单', JSON.stringify(await vals(p)));
  await p.screenshot({ path: SP + '/e_drum_voice.png' });
  await p.click('button[type=submit]'); await p.waitForTimeout(1200);
  log('  判定:', (await p.$eval('#verdict', e => e.innerText)).replace(/\n+/g, ' / ').slice(0, 160));
  await p.screenshot({ path: SP + '/e_drum_ng.png' });
  // 复测：厚度改 20 重新提交
  await p.fill('input[name=thick]', '20'); await p.click('button[type=submit]'); await p.waitForTimeout(1200);
  log('  复测判定:', (await p.$eval('#verdict', e => e.innerText)).replace(/\n+/g, ' / ').slice(0, 120));
  await b.close();

  // 4. 大车传动：语音说噪声，铭牌手填
  ({ b, p } = await browser('a3.wav'));
  await p.goto(B + '#/c/' + encodeURIComponent(ht) + '/travel_drive'); await p.waitForTimeout(1500);
  await speak(p, 'a3.wav', 3);
  await p.fill('input[name=reducer_nameplate]', 'ZQ500-样例'); await p.fill('input[name=motor_nameplate]', 'YZR160M2-6 样例');
  log('4 大车传动表单', JSON.stringify(await vals(p)));
  await p.click('button[type=submit]'); await p.waitForTimeout(1200);
  log('  判定:', (await p.$eval('#verdict', e => e.innerText)).replace(/\n+/g, ' / ').slice(0, 120));
  await p.screenshot({ path: SP + '/e_travel.png' });

  // 5. 详情页导出并下载
  await p.goto(B + '#/c/' + encodeURIComponent(ht)); await p.waitForTimeout(1000);
  log('5 合同状态', await p.$eval('.gate', e => e.innerText.replace(/\n+/g, ' / ')));
  await p.click('#exp'); await p.waitForSelector('#expres .note', { timeout: 20000 });
  log('  导出结果', await p.$eval('#expres', e => e.innerText.replace(/\n+/g, ' / ')));
  await p.screenshot({ path: SP + '/e_export.png' });
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#expres a')]);
  const file = SP + '/' + dl.suggestedFilename(); await dl.saveAs(file); log('  下载', file);
  await p.goto(B + '#/exports'); await p.waitForTimeout(1000); await p.screenshot({ path: SP + '/e_exports.png' });
  log('ERR', errs.length ? errs : 'none'); await b.close();
})();

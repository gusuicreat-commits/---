// 仅在独立验收库运行：真实 FunASR + 合成录音，覆盖已有合同弹窗四类语音录入。
const { chromium } = require(process.env.QC_PLAYWRIGHT || '/Users/mac/Desktop/代码解释器/node_modules/playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const B = process.env.QC_BASE || 'http://localhost:8602/';
const OUT = process.argv[2] || path.resolve(__dirname, '../../端到端截图/参数语音弹窗');
const HT = process.env.QC_TEST_CONTRACT || '20300102-10';
(async () => {
 fs.mkdirSync(OUT, { recursive: true });
 const browser = await chromium.launch({ channel: 'chrome', args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(__dirname, 'audio/a1.wav')}%noloop`] });
 try {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 662, height: 767 } });
  await ctx.grantPermissions(['microphone'], { origin: new URL(B).origin });
  const rq = ctx.request;
  assert.equal((await rq.post(B + 'api/contracts', { data: { ht_code: HT, name: '弹窗语音验收（样例）' } })).status(), 201);
  const p = await ctx.newPage(), errors = [];
  p.on('pageerror', e => errors.push(e.message));
  await p.addInitScript(() => {
   window.testStreams = [];
   const get = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
   navigator.mediaDevices.getUserMedia = async (...args) => { const stream = await get(...args); window.testStreams.push(stream); return stream; };
  });
  await p.goto(B + 'screen.html');
  await p.selectOption('#contractPageSize','100');
  await p.locator(`#contractList [data-ht="${HT}"]`).click();
  await p.click('#addBtn');
  await p.waitForFunction(() => document.getElementById('cvTitle').textContent === '卷筒参数语音填表' && !document.getElementById('cvMic').disabled);
  assert(await p.locator('#fNew').isHidden());
  await p.fill('#fv_length', '3000'); await p.fill('#fv_thick', '20');
  await p.locator('#cvMic').scrollIntoViewIfNeeded(); await p.waitForTimeout(350);
  const audioResponse = p.waitForResponse(r => r.url().endsWith('/api/voice/parse'));
  const mic = await p.locator('#cvMic').boundingBox();
  await p.mouse.move(mic.x + mic.width / 2, mic.y + mic.height / 2);
  await p.mouse.down(); await p.waitForTimeout(4800); await p.mouse.up();
  const audio = await (await audioResponse).json();
  await p.waitForFunction(() => document.getElementById('fv_diameter').value === '1250');
  assert.equal(await p.inputValue('#fv_light'), '300');
  assert.equal(await p.inputValue('#fv_length'), '3000'); assert.equal(await p.inputValue('#fv_thick'), '20');
  assert(await p.locator('#fw_diameter').evaluate(el => el.classList.contains('voice')));
  await p.locator('.dw-b').evaluate(el => el.scrollTop = 0);
  await p.screenshot({ path: path.join(OUT, '01_已有合同_卷筒语音.png') });
  const submit = async kind => {
   const response = p.waitForResponse(r => r.request().method() === 'POST' && r.url().includes(`/records/${kind}`));
   await p.click('#fGo'); const r = await response; assert.equal(r.status(), 201);
   const data = await r.json();
   await p.waitForFunction(() => !document.getElementById('fGo').disabled && document.getElementById('fRes').textContent.includes('记录 #'));
   return data;
  };
  const drum = await submit('drum');
  assert.equal(drum.record.is_qualified, 1); assert(drum.voice_ids.includes(audio.voice_id));
  const evidence = await (await rq.get(B + 'api/voice/logs/' + audio.voice_id)).json();
  assert.equal(evidence.record_kind, 'drum'); assert.equal(evidence.record_id, drum.record.id);
  const typed = async text => {
   await p.fill('#cvText', text); await p.click('#cvParse');
   await p.waitForFunction(() => !document.getElementById('cvParse').disabled && document.getElementById('cvStatus').textContent.includes('已填入'));
  };
  await typed('直径1262'); assert(await p.locator('#fw_diameter').evaluate(el => el.classList.contains('ng')));
  for (const [kind, title, text, values] of [
   ['travel_drive', '大车传动', '减速机铭牌 ZQ500，电机铭牌 YZR160，噪音82dB', { noise: '82', reducer_nameplate: 'ZQ500', motor_nameplate: 'YZR160' }],
   ['control_cabinet', '控制柜', '绝缘电阻五百兆欧，控制线力矩一点二牛米，电气间隙八毫米', { insulation_resistance: '500', control_wire_torque: '1.2', electrical_clearance: '8' }],
   ['travel_paint', '大车油漆', '底漆厚度约八十微米，面漆厚度99%，附着力1%', { primer_thickness_ratio: '约八十微米', total_thickness_ratio: '99', adhesion_loss_ratio: '1' }],
  ]) {
   await p.click(`#fKind [data-k="${kind}"]`);
   assert.equal(await p.locator('#cvTitle').textContent(), title + '参数语音填表');
   await typed(text);
   for (const [key, value] of Object.entries(values)) assert.equal(await p.inputValue('#fv_' + key), value);
   await p.locator('.dw-b').evaluate(el => el.scrollTop = 0);
   await p.screenshot({ path: path.join(OUT, '02_' + kind + '_语音回填.png') });
   const result = await submit(kind); assert.equal(result.record.is_qualified, 1); assert(result.voice_ids.length);
  }
  assert.equal((await (await rq.get(B + 'api/contracts/' + HT)).json()).status, '合格');
  // 切换类别后迟到的结果不得回填到另一张表。
  await p.click('#fKind [data-k="drum"]');
  let arrived, release; const started = new Promise(r => arrived = r), gate = new Promise(r => release = r);
  await p.route('**/api/voice/parse-text', async route => { arrived(); await gate; await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: '迟到结果', fields: { diameter: '999' }, rejected: [] }) }); });
  await p.fill('#cvText', '直径999'); await p.click('#cvParse'); await started;
  await p.click('#fKind [data-k="control_cabinet"]'); release(); await p.waitForTimeout(300);
  assert.equal(await p.inputValue('#fv_insulation_resistance'), ''); assert(await p.locator('#cvResult').isHidden());
  await p.unroute('**/api/voice/parse-text');
  // 新建合同原有语音入口仍可填写合同信息。
  await p.selectOption('#fCt', '__new__');
  await p.waitForFunction(() => document.getElementById('cvTitle').textContent === '合同语音填表' && document.getElementById('nDate').value);
  await typed('合同号20300102-20，客户弹窗语音样例，日期今天');
  assert.equal(await p.inputValue('#nHt'), '20300102-20'); assert.equal(await p.inputValue('#nName'), '弹窗语音样例');
  // 关闭弹窗时停止录音并释放麦克风。
  await p.waitForFunction(() => !document.getElementById('cvMic').disabled);
  await p.locator('#cvMic').focus(); await p.keyboard.down('Space');
  await p.waitForFunction(() => document.getElementById('cvMic').classList.contains('rec'));
  await p.keyboard.press('Escape'); await p.keyboard.up('Space'); await p.waitForTimeout(200);
  assert(await p.evaluate(() => window.testStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ contract: HT, realASR: audio.text, fourForms: 'passed', evidence: 'linked', contractVoice: 'passed', cancelAndSwitch: 'passed', pageErrors: errors, screenshots: OUT }, null, 2));
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

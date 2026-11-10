// 四类必检参数浏览器验收；只在独立验收库运行，阈值为 seed_demo 的样例值。
const { chromium } = require(process.env.QC_PLAYWRIGHT || '/Users/mac/Desktop/代码解释器/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const B = process.env.QC_BASE || 'http://localhost:8602/';
const OUT = process.argv[2] || '端到端截图/四类参数';
const HT = process.env.QC_TEST_CONTRACT || '20300101-10';
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', args: [
    '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${path.join(__dirname, 'audio/a1.wav')}%noloop`,
  ] });
  try {
    const sc = await browser.newContext({ viewport: { width: 1600, height: 1000 }, ignoreHTTPSErrors: true });
    const ph = await browser.newContext({ viewport: { width: 393, height: 852 }, ignoreHTTPSErrors: true });
    await ph.grantPermissions(['microphone'], { origin: new URL(B).origin });
    const rq = sc.request, errors = [];
    const created = await rq.post(B + 'api/contracts', { data: { ht_code: HT, name: '四类参数验收（样例）' } });
    assert.equal(created.status(), 201, await created.text());
    const S = await sc.newPage(), P = await ph.newPage();
    [S, P].forEach(p => p.on('pageerror', e => errors.push(e.message)));
    // 模拟已有用户仅保存了两类草稿，升级后仍可直接切换新增表单。
    await P.addInitScript(ht => {
      localStorage.setItem('qcp_ht', JSON.stringify(ht));
      localStorage.setItem('qcp_draft', JSON.stringify({ drum: {}, travel_drive: {} }));
      localStorage.setItem('qcp_vids', JSON.stringify({ drum: [], travel_drive: [] }));
    }, HT);
    await S.goto(B + 'screen.html');
    await P.goto(B + 'phone.html');
    await S.waitForFunction(ht => document.getElementById('detailContract').textContent === ht, HT);
    await P.waitForSelector('input[data-f="diameter"]');
    assert.equal(await P.locator('#tabs button').count(), 4);
    await P.waitForFunction(() => !document.getElementById('mic').classList.contains('off'));
    const mic = await P.locator('#mic').boundingBox();
    await P.mouse.move(mic.x + mic.width / 2, mic.y + mic.height / 2);
    await P.mouse.down(); await P.waitForTimeout(4800); await P.mouse.up();
    await P.waitForFunction(() => document.querySelector('input[data-f="diameter"]').value === '1250', null, { timeout: 30000 });
    await P.fill('input[data-f="length"]', '3000');
    await P.fill('input[data-f="thick"]', '20');
    await P.click('#sub');
    await P.waitForFunction(() => document.getElementById('banner').textContent.includes('卷筒参数合格'));
    // 同义词 + dB：主管代录，手机收到语音字段，质检员确认提交。
    await P.click('#tabs [data-k="travel_drive"]');
    await S.waitForFunction(() => document.querySelector('#mChips').textContent.includes('噪音'));
    await S.fill('#mInput', '噪音八十二dB'); await S.press('#mInput', 'Enter');
    await P.waitForFunction(() => document.querySelector('input[data-f="noise"]').value === '82');
    await P.fill('input[data-f="motor_nameplate"]', 'YZR160 样例');
    await P.fill('input[data-f="reducer_nameplate"]', 'ZQ500 样例');
    await P.click('#sub');
    await P.waitForFunction(() => document.getElementById('banner').textContent.includes('大车传动参数合格'));
    assert.equal((await rq.post(B + `api/contracts/${HT}/export`)).status(), 409);
    // 控制柜在手机语音代录；油漆在大屏手动录入，覆盖两种录入路径。
    await P.click('#tabs [data-k="control_cabinet"]');
    await S.waitForFunction(() => document.querySelector('#mChips').textContent.includes('绝缘电阻'));
    await S.fill('#mInput', '绝缘电阻五百兆欧，控制线力矩一点二牛米，电气间隙八毫米');
    await S.press('#mInput', 'Enter');
    await P.waitForFunction(() => document.querySelector('input[data-f="electrical_clearance"]').value === '8');
    await P.waitForTimeout(500);
    await P.screenshot({ path: path.join(OUT, '01_手机_控制柜.png') });
    await P.click('#sub');
    await P.waitForFunction(() => document.getElementById('banner').textContent.includes('控制柜参数合格'));
    await S.click('#addBtn'); await S.click('#fKind [data-k="travel_paint"]');
    for (const [f, v] of Object.entries({ primer_thickness_ratio: '98', total_thickness_ratio: '99', adhesion_loss_ratio: '6' })) await S.fill('#fv_' + f, v);
    await S.click('#fGo');
    await S.waitForFunction(() => document.getElementById('fRes').textContent.includes('不合格'));
    assert.equal((await rq.post(B + `api/contracts/${HT}/export`)).status(), 409);
    await S.fill('#fv_adhesion_loss_ratio', '1'); await S.click('#fGo');
    await S.waitForFunction(() => document.getElementById('fRes').textContent.includes('大车油漆参数合格'));
    await S.screenshot({ path: path.join(OUT, '02_大屏_油漆复测.png') });
    await S.click('#dwX'); await S.waitForTimeout(500);
    await P.click('#tabs [data-k="travel_paint"]');
    await P.waitForTimeout(3500);
    await P.screenshot({ path: path.join(OUT, '03_手机_油漆.png') });
    await S.waitForFunction(() => document.getElementById('gateP').textContent.includes('四类'));
    await S.screenshot({ path: path.join(OUT, '04_大屏_四类合格.png') });
    await P.click('#recsBtn'); // 最近记录应包含四类
    await P.waitForFunction(() => document.getElementById('recs').textContent.includes('控制柜'));
    assert.match(await P.locator('#recs').textContent(), /控制柜/);
    assert.match(await P.locator('#recs').textContent(), /大车油漆/);
    const A = await sc.newPage(); A.on('pageerror', e => errors.push(e.message));
    await A.goto(B + `admin.html#/c/${HT}`);
    await A.waitForSelector('a[href$="/control_cabinet"]');
    assert.equal(await A.locator('a[href$="/travel_paint"]').count(), 1);
    await A.locator('a[href$="/travel_paint"]').click();
    await A.waitForSelector('input[name="primer_thickness_ratio"]');
    await A.fill('#vtxt', '底漆厚度约八十微米，面漆厚度99%，附着力1%');
    await A.click('#vgo');
    await A.waitForFunction(() => document.querySelector('input[name="primer_thickness_ratio"]').value === '约八十微米');
    await A.screenshot({ path: path.join(OUT, '05_后台_油漆语音.png') });
    await A.goto(B + 'admin.html#/rules');
    await A.waitForSelector('input');
    assert.match(await A.locator('#app').textContent(), /附着力/);
    await A.screenshot({ path: path.join(OUT, '06_后台_十条规则.png') });
    const exp = await rq.post(B + `api/contracts/${HT}/export`); assert.equal(exp.status(), 200);
    const result = await exp.json();
    assert.equal(result.sheets.length, 1); assert(result.sheets.every(s => s.ok));
    const day = result.file.split('_')[1].replace('.xlsx', '');
    const file = await rq.get(B + `api/exports/${HT}/${day}`);
    fs.writeFileSync(path.join(OUT, result.file), await file.body());
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ contract: HT, sheets: result.sheets.map(s => s.name), pageErrors: errors, screenshots: OUT }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

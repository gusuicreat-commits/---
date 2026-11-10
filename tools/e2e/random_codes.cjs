// 仅对独立临时数据库服务验收，不连接活动 8443 数据库。
const { chromium } = require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const base = process.env.QC_BASE || 'http://127.0.0.1:8602/';
assert.equal(new URL(base).port, '8602', '必须使用独立验收服务');
const output = path.resolve(__dirname, '../../端到端截图/随机合同号');
const profile = require('../../config/showcase.json');
const valid = code => assert.match(code, /^2026[0-9]{4}$/);

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1086, height: 767 } });
    const request = context.request, errors = [];
    const rules = (await (await request.get(base + 'api/rules')).json()).items;
    for (const rule of rules) {
      const spec = profile.rules.find(r => r.table === rule.table_name && r.field === rule.name);
      assert.equal((await request.put(base + 'api/rules/' + rule.id, {
        data: { min: spec.min, max: spec.max }
      })).status(), 200);
    }
    // 旧规则当天九个号全部占用，也能新建并启动演示。
    for (let n = 10; n < 100; n += 10) {
      assert.equal((await request.post(base + 'api/contracts', {
        data: { ht_code: '20261110-' + n, name: '历史号码兼容（测试）' }
      })).status(), 201);
    }
    const admin = await context.newPage();
    admin.on('pageerror', e => errors.push(e.message));
    await admin.goto(base + 'admin.html#/new');
    await admin.waitForSelector('input[name="ht_code"]');
    valid(await admin.inputValue('input[name="ht_code"]'));
    await admin.fill('#vtxt', '合同号二零二六零零零七，客户样例重工');
    await admin.click('#vgo');
    await admin.waitForFunction(() => document.querySelector('[name=ht_code]').value === '20260007');
    await admin.screenshot({ path: path.join(output, '01_后台语音新编号.png') });
    await admin.click('#f button[type=submit]');
    await admin.waitForURL('**/admin.html#/c/20260007');
    // 重号返回新的可用随机建议。
    const duplicate = await request.post(base + 'api/contracts', {
      data: { ht_code: '20260007', name: '重复测试' }
    });
    assert.equal(duplicate.status(), 409);
    const suggestion = (await duplicate.json()).error.suggestion;
    valid(suggestion); assert.notEqual(suggestion, '20260007');

    const screen = await context.newPage();
    screen.on('pageerror', e => errors.push(e.message));
    await screen.goto(base + 'screen.html');
    await screen.click('#addBtn');
    await screen.selectOption('#fCt', '__new__');
    await screen.waitForFunction(() => /^2026\d{4}$/.test(document.getElementById('nHt').value));
    const screenCode = await screen.inputValue('#nHt'); valid(screenCode);
    await screen.fill('#nName', '大屏随机编号（测试）');
    for (const [key, value] of Object.entries(profile.normal.drum)) await screen.fill('#fv_' + key, value);
    await screen.screenshot({ path: path.join(output, '02_大屏随机编号.png') });
    const saved = screen.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/records/drum'));
    await screen.click('#fGo'); assert.equal((await saved).status(), 201);

    const phone = await context.newPage();
    await phone.setViewportSize({ width: 390, height: 844 });
    phone.on('pageerror', e => errors.push(e.message));
    await phone.goto(base + 'phone.html');
    await phone.click('#ctBtn');
    await phone.waitForFunction(() => /^2026\d{4}$/.test(document.getElementById('ncCode').value));
    const phoneCode = await phone.inputValue('#ncCode'); valid(phoneCode);
    assert.notEqual(phoneCode, screenCode);
    await phone.fill('#ncName', '手机随机编号（测试）');
    await phone.screenshot({ path: path.join(output, '03_手机随机编号.png') });
    const created = phone.waitForResponse(r => r.request().method() === 'POST' && /\/api\/contracts\?/.test(r.url()));
    await phone.click('#ncGo'); assert.equal((await created).status(), 201);

    // 新编号贯穿四类记录和 Excel 下载；历史合同详情保持原号。
    for (const [kind, fields] of Object.entries(profile.normal)) {
      assert.equal((await request.post(base + `api/contracts/20260007/records/${kind}`, { data: fields })).status(), 201);
    }
    const exported = await request.post(base + 'api/contracts/20260007/export');
    assert.equal(exported.status(), 200);
    assert.equal((await exported.json()).sheets.length, 1);
    const file = (await (await request.get(base + 'api/exports')).json()).items.find(x => x.ht_code === '20260007');
    const download = await request.get(base + `api/exports/20260007/${file.date.replaceAll('-', '')}`);
    assert.equal(download.status(), 200);
    assert.equal((await (await request.get(base + 'api/contracts/20261110-10')).json()).contract.ht_code, '20261110-10');
    const demo = await request.post(base + 'api/demo/start', { data: { speed: 2 } });
    assert.equal(demo.status(), 200); valid((await demo.json()).ht_code);
    await request.post(base + 'api/demo/stop');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ admin: '20260007', screen: screenCode, phone: phoneCode,
      exportSheets: 1, legacyPreserved: true, pageErrors: errors.length }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

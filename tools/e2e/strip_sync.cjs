// 独立数据库副本验收：左侧仅合同与录入，中间明细与实际记录一致，异步响应不串合同。
const {chromium}=require(process.env.QC_PLAYWRIGHT||'/Users/mac/Desktop/代码解释器/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const B=process.env.QC_BASE||'http://localhost:8602/';
const OUT=process.argv[2]||path.resolve(__dirname,'../../端到端截图/中间参数明细');
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});const browser=await chromium.launch({channel:'chrome'});
 try{
  const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1086,height:767}}),p=await ctx.newPage(),rq=ctx.request,errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  await p.goto(B+'screen.html');await p.waitForFunction(()=>QC.bus.mode==='live');
  const A='20261108-20',C='20261106-30';
  await p.selectOption('#contractPageSize','100');
  const choose=ht=>p.locator(`#contractList [data-ht="${ht}"]`).click();
  const same=async(ht,kind,label,overrides={})=>{
   await p.waitForFunction(([ht,kind,label])=>S.detail?.contract.ht_code===ht&&S.form===kind&&document.getElementById('detailContext').textContent.includes(label),[ht,kind,label]);
   const detail=await(await rq.get(B+'api/contracts/'+ht)).json();
   const data=await p.locator(`#rows .row[data-k="${kind}"]`).evaluateAll(rows=>rows.map(row=>({field:row.dataset.f,value:row.querySelector('.val b')?.textContent||null})));
   for(const row of data)assert.equal(row.value,overrides[row.field]??detail.latest[kind]?.[row.field]??null,`${ht}/${kind}/${row.field}`);
   assert.equal(await p.locator('.contract-nav #rows,.contract-nav #sheetN,.contract-nav #ctName').count(),0);
   assert.equal(await p.locator('#stage #rows').count(),1);
   assert.equal(await p.locator('#strip').count(),0);
   assert.equal(await p.locator('#rows .row').count(),13);
   assert.match(await p.locator('#rows').textContent(),new RegExp(label+' · 当前'));
  };
  await choose(A);await same(A,'drum','卷筒');
  // 复现截图：空的合同语音不应把当前参数摘要改成“表单=合同”。
  await rq.post(B+'api/voice/parse-text',{data:{text:'你好',form:'contract',source:'screen'}});
  await p.waitForFunction(()=>document.getElementById('tr').textContent.includes('你好'));
  await same(A,'drum','卷筒');
  // 识别动画尚未结束时切走，不能恢复旧识别/旧回读。
  await choose(C);await same(C,'drum','卷筒');await p.waitForTimeout(1500);
  assert.match(await p.locator('#tr').textContent(),/等待/);
  assert.equal(await p.locator('#rbp').getAttribute('class'),'idle');
  assert(!/你好|未识别到参数/.test(await p.locator('#rbp').textContent()));
  // 同合同切换草稿类别：同时显示草稿值和已保存的其余字段。
  await rq.post(B+'api/events',{data:{type:'draft',source:'phone',data:{ht_code:C,form:'travel_drive',fields:{noise:'91'}}}});
  await same(C,'travel_drive','大车传动',{noise:'91'});
  assert.match(await p.locator('#detailContext').textContent(),/草稿 · 未提交/);
  assert.equal(await p.locator('#rows .row[data-k="travel_drive"][data-f="noise"] .val b').evaluate(el=>el.childNodes[0].textContent.trim()),'91');
  const record=await(await rq.post(B+`api/contracts/${C}/records/travel_drive?source=screen`,{data:{noise:'82',reducer_nameplate:'ZQ500（样例）',motor_nameplate:'YZR160（样例）'}})).json();
  await p.waitForFunction(id=>document.getElementById('detailContext').textContent.includes('已保存 #'+id),record.record.id);
  await same(C,'travel_drive','大车传动');
  assert(!/91|未提交/.test(await p.locator('#detailContext').textContent()));
  // 没有语音的手动录入也必须更新当前类别和全部数值。
  const cabinet=await(await rq.post(B+`api/contracts/${C}/records/control_cabinet?source=screen`,{data:{insulation_resistance:'180',control_wire_torque:'1.05',electrical_clearance:'11'}})).json();
  await p.waitForFunction(id=>document.getElementById('detailContext').textContent.includes('已保存 #'+id),cabinet.record.id);
  await same(C,'control_cabinet','控制柜');
  await p.screenshot({path:path.join(OUT,'01_参数明细在中间.png')});
  await p.setViewportSize({width:1440,height:900});
  await p.screenshot({path:path.join(OUT,'02_宽屏明细.png')});
  await p.setViewportSize({width:1086,height:767});
  const box=await p.locator('#stage').boundingBox();assert(box.width>400);
  await p.locator('.voice-panel summary').click();await p.waitForFunction(()=>document.querySelector('.voice-panel').open);
  await p.locator('.voice-panel summary').click();
  await p.locator('#rows .row[data-k="control_cabinet"][data-f="electrical_clearance"]').click();
  await p.waitForFunction(()=>document.getElementById('dw').classList.contains('on'));
  assert.equal(await p.locator('#fKind button.on').getAttribute('data-k'),'control_cabinet');
  await p.click('#dwX');
  // 新记录比上句识别动画晚到，但必须保留新记录显示。
  await rq.post(B+'api/voice/parse-text',{data:{text:'噪声91',form:'travel_drive',ht_code:C,source:'screen'}});
  await p.waitForFunction(()=>document.getElementById('stage').classList.contains('busy'));
  const newer=await(await rq.post(B+`api/contracts/${C}/records/travel_drive?source=screen`,{data:{noise:'80',reducer_nameplate:'ZQ500（样例）',motor_nameplate:'YZR160（样例）'}})).json();
  await p.waitForFunction(id=>document.querySelector('#hero .h-item')?.textContent.includes('记录 #'+id),newer.record.id);
  await p.waitForTimeout(1600);await same(C,'travel_drive','大车传动');
  assert.match(await p.locator('#hero .h-item').textContent(),new RegExp('记录 #'+newer.record.id));
  assert.match(await p.locator('#detailContext').textContent(),/已保存/);
  // 模拟合同A的迟到详情，不能覆盖随后切到的合同C。
  let arrived,release;const started=new Promise(r=>arrived=r),gate=new Promise(r=>release=r);
  await p.route('**/api/contracts/'+A,async route=>{arrived();await gate;await route.continue();});
  await choose(A);await started;await choose(C);
  await same(C,'travel_drive','大车传动');release();await p.waitForTimeout(300);await same(C,'travel_drive','大车传动');
  assert.match(await p.locator('#ctName').textContent(),/长风电力检修/);
  await p.unroute('**/api/contracts/'+A);
  await p.screenshot({path:path.join(OUT,'02_快速切换后内容不串合同.png')});
  await p.evaluate(()=>focus(null));
  await p.waitForFunction(()=>!S.ht);
  assert.match(await p.locator('#detailContext').textContent(),/未选择合同/);
  assert(!/202611|ZQ500|长风/.test(await p.locator('#detailContext').textContent()));
  await p.route('**/api/contracts/'+A,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'DB_UNAVAILABLE',message:'模拟读取失败'}})}));
  await choose(A);
  await p.waitForFunction(()=>document.getElementById('detailContext').textContent.includes('详情暂不可用'));
  assert(!/尚未录入|已保存/.test(await p.locator('#detailContext').textContent()));
  assert.deepEqual(errors,[]);
  console.log('通过：逐字段一致、切合同清旧提示、手机草稿、手动提交更新类别、旧识别动画不覆盖新记录、迟到详情不串合同、取消选择清空；页面异常0。');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

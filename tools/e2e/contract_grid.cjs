// 独立验收服务：合同分页、状态、田字布局和点击后的逐字段一致性。
const {chromium}=require('/Users/mac/Desktop/代码解释器/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const B=process.env.QC_BASE||'http://127.0.0.1:8602/';
assert.equal(new URL(B).port,'8602','请使用独立验收库');
const OUT=path.resolve(__dirname,'../../端到端截图/合同分页与田字布局');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 const ctx=await browser.newContext({viewport:{width:1086,height:767}}),p=await ctx.newPage(),rq=ctx.request,errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 const items=(await(await rq.get(B+'api/contracts')).json()).items;assert(items.length>20&&items.length<=50);
 await p.goto(B+'screen.html');await p.waitForFunction(()=>QC.bus.mode==='live');
 assert.equal(await p.locator('#contractPageSize').inputValue(),'20');
 const Q=items.slice(20).find(c=>c.status==='合格').ht_code;
 const label=status=>status==='合格'?'合格':status==='不合格'?'不合格':'未录入';
 const checkPage=async(page,size)=>{
  const expected=items.slice((page-1)*size,page*size);
  await p.waitForFunction(n=>document.querySelectorAll('#contractList .contract-item').length===n,expected.length);
  assert.deepEqual(await p.locator('#contractList .contract-item').evaluateAll(nodes=>nodes.map(n=>({ht:n.dataset.ht,status:n.querySelector('.contract-status').textContent}))),expected.map(c=>({ht:c.ht_code,status:label(c.status)})));
 };
 await checkPage(1,20);assert(await p.locator('#contractPrev').isDisabled());
 const same=async ht=>{
  await p.waitForFunction(ht=>document.getElementById('detailContract').textContent===ht&&S.detail?.contract.ht_code===ht,ht);
  const detail=(await(await rq.get(B+'api/contracts/'+ht)).json());
  const fields=await p.locator('#rows .row').evaluateAll(nodes=>nodes.map(n=>({kind:n.dataset.k,name:n.dataset.f,value:n.querySelector('.val b')?.textContent??null})));
  assert.equal(fields.length,13);
  for(const f of fields)assert.equal(f.value,detail.latest[f.kind]?.[f.name]??null,`${ht}/${f.kind}/${f.name}`);
  assert.equal(await p.locator('#rows>.parameter-card').count(),4);
  assert.equal(await p.locator(`#contractList [data-ht="${ht}"]`).getAttribute('aria-pressed'),'true');
 };
 await p.locator('#contractList .contract-item').first().click();await same(items[0].ht_code);
 await p.click('#contractNext');await checkPage(2,20);
 assert.equal(await p.locator('#detailContract').textContent(),items[0].ht_code); // 翻页本身不误切合同。
 await p.locator('#contractList .contract-item').last().click();await same(items.at(-1).ht_code);
 assert(await p.locator('#contractNext').isDisabled());assert(await p.locator('#contractPrev').isEnabled());
 await p.locator(`#contractList [data-ht="${Q}"]`).click();await same(Q);
 const boxes=await p.locator('#rows>.parameter-card').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};}));
 assert.equal(boxes[0].y,boxes[1].y);assert.equal(boxes[2].y,boxes[3].y);assert.equal(boxes[0].x,boxes[2].x);assert.equal(boxes[1].x,boxes[3].x);assert(boxes[2].y>boxes[0].y);
 const bottom=await p.locator('#rows').evaluate(el=>el.getBoundingClientRect().bottom);assert(boxes[3].y+boxes[3].h<=bottom+1,'四块参数应完整显示在明细区域内');
 assert.equal(await p.locator('#ctSel').count(),0);
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 fs.mkdirSync(OUT,{recursive:true});await p.screenshot({path:path.join(OUT,'01_二十条分页与田字参数.png')});
 await p.setViewportSize({width:1440,height:900});await p.screenshot({path:path.join(OUT,'02_宽屏田字参数.png')});
 await p.setViewportSize({width:1086,height:767});
 await p.selectOption('#contractPageSize','50');await checkPage(1,50);
 assert(await p.locator('#contractPrev').isDisabled());assert(await p.locator('#contractNext').isDisabled());
 await p.selectOption('#contractPageSize','20');await checkPage(2,20);await same(Q);
 await p.click('#contractPrev');await checkPage(1,20);
 await p.click('#addBtn');await p.waitForFunction(()=>document.getElementById('dw').classList.contains('on'));await p.click('#dwX');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({defaultPageSize:20,contracts:items.length,statuses:true,allFourPanels:true,clickMatchesApi:true,grid:true,pageSizeChange:true,pageErrors:0}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

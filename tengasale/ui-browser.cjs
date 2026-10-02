const {chromium} = require('C:/Users/CHRIS PAUL MWALE/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
(async()=>{
const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const page = await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));const results=[];
for(const name of ['landing','hq']) {
 await page.goto('http://127.0.0.1:8765/ui-preview/'+name+'.html',{waitUntil:'networkidle'});
 for(const width of [360,390,768,1024,1366,1440]){
  await page.setViewportSize({width,height:900});await page.waitForTimeout(100);
  results.push({page:name,width,...await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,viewport:innerWidth,broken:[...document.images].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.src),map:document.querySelector('.approved-market-map')?.getBoundingClientRect().width}))});
 }
 await page.screenshot({path:'ui-preview/'+name+'-desktop.png',fullPage:false});
 if(name==='landing'){
  const card=page.locator('.phone-card').first();await card.getByRole('radio',{name:'weekly',exact:true}).click();results.push({weekly:await card.locator('.phone-pricing__selected strong').innerText()});
  await card.getByRole('radio',{name:'monthly',exact:true}).click();results.push({monthly:await card.locator('.phone-pricing__selected strong').innerText()});
  await card.getByRole('button',{name:/Choose/}).click();results.push({selection:await page.locator('#selectedPhone').innerText()});
  await page.locator('#solar a').click();results.push({solarSubject:await page.locator('#id_subject').inputValue()});
  for(const country of ['malawi','zambia','zimbabwe','kenya']){await page.locator('.approved-market-bar [data-market='+country+']').click();results.push({country:await page.locator('#approvedCountry').innerText()});}
  await page.locator('#lockButton').click();results.push({simulation:await page.locator('#lockUi').getAttribute('data-state')});
 } else {await page.locator('#hq-all-tools summary').click();results.push({directory:await page.locator('#hq-all-tools').getAttribute('open')});}
}
console.log(JSON.stringify({results,errors},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1});

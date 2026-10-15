const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const dir=path.join(__dirname,'..','extension');
const code=fs.readFileSync(path.join(dir,'capture.js'),'utf8');
const html=`<html><head><title>Appartement 43 m²</title><meta property="og:description" content="Appartement 234000 euros"><script type="application/ld+json">{"@type":"Apartment","offers":{"price":234000,"priceCurrency":"EUR"}}</script></head><body><header>Mon compte personnel</header><main><p id="select">Prix : 234 000 €. Surface habitable : 43 m². Appartement ancien à vendre.</p><div class="price" onclick="bad()">234 000 €</div><div hidden>Texte privé</div><div style="display:none">Secret CSS</div><form><input value="mot-de-passe"></form><script>bad()</script></main></body></html>`;

test('collecte HTML nettoyé, texte visible et JSON-LD',async()=>{
 const dom=new JSDOM(html,{runScripts:'outside-only',url:'https://example.com/annonce'});
 const r=await dom.window.eval(code); // capture.js est asynchrone (relecture Leboncoin)
 assert.match(r.text,/234 000/);assert.match(r.html,/class="price"/);assert.equal(r.json_ld.length,1);
 assert.doesNotMatch(r.html,/onclick|mot-de-passe|bad\(|Texte privé|Secret CSS/);
 assert.doesNotMatch(r.text,/Mon compte|Texte privé|Secret CSS/);
 dom.window.close();
});

test('sélection manuelle exclut HTML et métadonnées',async()=>{
 const dom=new JSDOM(html,{runScripts:'outside-only',url:'https://example.com/annonce'});
 const range=dom.window.document.createRange();range.selectNodeContents(dom.window.document.getElementById('select'));dom.window.getSelection().addRange(range);
 const r=await dom.window.eval(code);assert.equal(r.selection_only,true);assert.equal(r.html,'');assert.equal(r.json_ld.length,0);assert.equal(Object.keys(r.metadata).length,0);dom.window.close();
});

const tick=()=>new Promise(r=>setImmediate(r));
async function popup(status=200){
 const dom=new JSDOM(fs.readFileSync(path.join(dir,'popup.html'),'utf8'),{runScripts:'outside-only',url:'chrome-extension://'+'a'.repeat(32)+'/popup.html'});
 const win=dom.window,requests=[],opened=[];
 win.chrome={storage:{local:{get:async()=>({repereToken:'token-de-test-local'}),set:async()=>{}}},tabs:{query:async()=>[{id:1,url:'https://example.com/annonce'}],create:async v=>opened.push(v)},scripting:{executeScript:async()=>[{result:{url:'https://example.com/annonce',title:'Appartement',text:'Prix : 234 000 €. Surface habitable : 43 m².',html:'<main>Annonce</main>',metadata:{description:'Annonce'},json_ld:[],selection_only:false,capture_warnings:[]}}]}};
 win.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body),headers:options.headers});return {ok:status===200,json:async()=>status===200?url.endsWith('/api/preview')?{facts:{price_eur:234000},warnings:[]}:{ok:true,listing_id:'id-test',listing:{report:{facts:{price_eur:234000},warnings:[]}}}:{detail:'Code incorrect'}}};
 win.eval(fs.readFileSync(path.join(dir,'popup.js'),'utf8'));await tick();
 return {dom,win,requests,opened,$:id=>win.document.getElementById(id)};
}

test('aperçu reste local puis envoi unique avec Gemini explicite',async()=>{
 const t=await popup();t.$('read').click();await tick();t.$('gemini').checked=true;t.$('check').click();await tick();
 assert.equal(t.requests[0].body.use_gemini,false);assert.match(t.$('facts').textContent,/234/);
 t.$('send').click();await tick();assert.equal(t.requests[1].body.use_gemini,true);assert.equal(t.requests[1].body.html,'<main>Annonce</main>');
 assert.equal(t.requests[1].headers['X-Repere-Token'],'token-de-test-local');t.$('open').click();assert.match(t.opened[0].url,/#annonce\/id-test/);t.dom.window.close();
});

test('édition du texte supprime les anciennes métadonnées à l’envoi',async()=>{
 const t=await popup();t.$('read').click();await tick();t.$('text').value+=' Balcon.';t.$('text').dispatchEvent(new t.win.Event('input'));t.$('send').click();await tick();
 assert.equal(t.requests[0].body.selection_only,true);assert.equal(t.requests[0].body.html,'');assert.deepEqual(t.requests[0].body.metadata,{});t.dom.window.close();
});

test('échec serveur affiché sans fausse confirmation',async()=>{
 const t=await popup(401);t.$('read').click();await tick();t.$('send').click();await tick();assert.equal(t.$('status').textContent,'Code incorrect');t.dom.window.close();
});

test('permissions limitées au clic et au service local',()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json')));
 assert.deepEqual(manifest.permissions,['activeTab','scripting','storage']);assert.deepEqual(manifest.host_permissions,['http://127.0.0.1/*']);
});

test('Leboncoin : annonce lue dans __NEXT_DATA__, sans données du vendeur',async()=>{
 const sale=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures','leboncoin_vente.json'),'utf8')).ad;
 const ad={...sale,index_date:'2025-11-02 08:15:00',images:{urls:sale.images},owner:{type:'private',name:'Nom du vendeur',user_id:'compte-123'}};
 const page=`<html><head><title>Annonce | leboncoin</title></head><body><main><h1>${ad.subject}</h1><p>379 000 €</p></main><script id="__NEXT_DATA__" type="application/json">${JSON.stringify({props:{pageProps:{ad}}})}</script></body></html>`;
 const dom=new JSDOM(page,{runScripts:'outside-only',url:'https://www.leboncoin.fr/ad/ventes_immobilieres/'+ad.list_id});
 const r=await dom.window.eval(code);
 assert.equal(r.site_data.source,'leboncoin');assert.equal(r.site_data.ad.list_id,ad.list_id);assert.equal(r.site_data.ad.first_publication_date,'2025-09-24 19:31:00');assert.equal(r.site_data.ad.index_date,'2025-11-02 08:15:00');assert.deepEqual(Array.from(r.site_data.ad.price),[379000]);
 assert.doesNotMatch(JSON.stringify(r.site_data),/Nom du vendeur|compte-123/);assert.ok(r.images[0].startsWith('https://img.leboncoin.fr/'));
 dom.window.close();
});

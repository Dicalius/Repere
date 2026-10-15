(async () => {
  // Leboncoin publie l'annonce complète dans <script id="__NEXT_DATA__"> (props.pageProps.ad).
  // On n'en garde que ce qui décrit le bien : ni nom du vendeur, ni identifiant de compte, ni téléphone.
  // Liste alignée sur reduce_ad() côté serveur (app/parsing/sites/leboncoin.py).
  const DROPPED = new Set(['profile_picture_url','store_logo','store_name','online_store_id','external_ad_id','custom_ref',
    'activity_sector','has_visibility_option','is_import','district_visibility','district_type_id','district_resolution_type',
    'district_id','new_seller_type']);
  const cut = (v, n) => v == null ? null : String(v).slice(0, n);
  function reduceLeboncoinAd(ad) {
    const attributes = (ad.attributes || []).filter(a => a && a.key && !DROPPED.has(a.key)).slice(0, 80).map(a => {
      const out = {key: cut(a.key, 300)};
      for (const k of ['key_label', 'value', 'value_label']) if (a[k] != null) out[k] = cut(a[k], 300);
      if (Array.isArray(a.values)) out.values = a.values.slice(0, 20).map(v => cut(v, 100));
      return out;
    });
    const loc = ad.location || {}, images = ad.images || {};
    const location = {};
    for (const k of ['city','zipcode','city_label','district','department_name','region_name','lat','lng','source']) if (loc[k] != null && loc[k] !== '') location[k] = loc[k];
    return {source: 'leboncoin', ad: {
      list_id: ad.list_id, subject: cut(ad.subject, 300), body: cut(ad.body, 20000),
      price: (ad.price || []).filter(p => typeof p === 'number').slice(0, 2),
      price_cents: typeof ad.price_cents === 'number' ? ad.price_cents : null,
      category_id: cut(ad.category_id, 10), category_name: cut(ad.category_name, 100), ad_type: cut(ad.ad_type, 20),
      url: cut(ad.url, 300), first_publication_date: cut(ad.first_publication_date, 40), index_date: cut(ad.index_date, 40),
      owner_type: cut(ad.owner?.type, 20),
      location, images: (images.urls_large || images.urls || []).filter(u => typeof u === 'string').slice(0, 5), attributes}};
  }
  let siteData = null, siteWarning = '';
  if (/(^|\.)leboncoin\.fr$/.test(location.hostname)) {
    const id = (location.pathname.match(/\/(\d{6,12})(?:\.htm)?\/?$/) || [])[1];
    const readAd = doc => {
      try {
        const ad = JSON.parse(doc.querySelector('script#__NEXT_DATA__')?.textContent || 'null')?.props?.pageProps?.ad;
        return ad && typeof ad === 'object' && (!id || String(ad.list_id) === id) ? ad : null;
      } catch (_) { return null; }
    };
    let ad = id ? readAd(document) : null;
    if (!ad && id) {
      // Arrivée par navigation interne : __NEXT_DATA__ décrit encore la page précédente (recherche).
      // On relit la page de l'annonce une fois, avec la session du navigateur ; aucun script n'est exécuté.
      try {
        const response = await fetch(location.href, {credentials: 'include', headers: {Accept: 'text/html'}});
        if (response.ok) ad = readAd(new DOMParser().parseFromString(await response.text(), 'text/html'));
      } catch (_) { /* analyse générique */ }
    }
    if (ad) siteData = reduceLeboncoinAd(ad);
    else if (id) siteWarning = 'Données Leboncoin introuvables : analyse générique de la page. Recharge l’annonce (F5) puis recapture-la.';
  }
  const selected = String(window.getSelection() || '').trim();
  let warnings_pending = '';
  const selectionOnly = selected.length >= 15;
  const root = document.querySelector('main,[role="main"]') || document.body;
  if (!root) throw new Error('Aucun contenu sur cette page.');
  // Les <header>/<footer>/<aside> ne sont exclus que hors du contenu principal (le <header> d'une
  // annonce porte souvent titre, prix et ville). Un <form> n'est plus exclu : certains sites
  // (ASP.NET WebForms) enveloppent toute la page dedans ; ses champs restent exclus.
  const excluded = 'script,style,noscript,svg,nav,input,textarea,select,button,[hidden],[aria-hidden="true"]';
  // Listes d'autres annonces (similaires, à proximité, recommandées…) : conteneurs qui regroupent au moins
  // trois « cartes » (lien vers une autre page accompagné d'un prix) et dont ces cartes forment l'essentiel
  // du texte. Le conteneur qui porte le titre de l'annonce (h1) n'est jamais écarté.
  const anchor = document.querySelector('h1');
  const here = location.href.split('#')[0];
  const priceRe = /\d[\d\s\u00a0\u202f.]{2,}\s?€/;
  const external = a => /^https?:/.test(a.href) && a.href.split('#')[0] !== here;
  const destinations = el => new Set([...el.querySelectorAll('a[href]')].filter(external).map(a => a.href.split('#')[0])).size;
  const cards = new Set();
  for (const a of root.querySelectorAll('a[href]')) {
    if (!external(a) || (anchor && a.contains(anchor))) continue;
    // Une carte = le plus grand bloc autour du lien qui ne mène qu'à cette destination.
    let card = a;
    while (card.parentElement && card.parentElement !== root && (card.parentElement.textContent || '').length <= 600 &&
           destinations(card.parentElement) <= 1 && !(anchor && card.parentElement.contains(anchor))) card = card.parentElement;
    if (priceRe.test(card.textContent || '')) cards.add(card);
  }
  const listStats = new Map();
  for (const card of cards) {
    const length = (card.textContent || '').length;
    for (let e = card.parentElement; e && e !== root; e = e.parentElement) {
      const s = listStats.get(e) || {n: 0, text: 0}; s.n += 1; s.text += length; listStats.set(e, s);
    }
  }
  const adLists = new Set([...listStats].filter(([e, s]) => s.n >= 3 && s.text >= 0.5 * (e.textContent || '').length &&
    !(anchor && e.contains(anchor))).map(([e]) => e));
  // Cartes mêlées au contenu de l'annonce (même parent que la fiche) : écartées une à une, par groupes d'au moins trois.
  const siblings = new Map();
  for (const card of cards) siblings.set(card.parentElement, [...(siblings.get(card.parentElement) || []), card]);
  for (const group of siblings.values()) if (group.length >= 3) group.forEach(card => adLists.add(card));
  const selfExcluded = e => adLists.has(e) || e.matches(excluded) ||
    (e.matches('header,footer,aside') && !e.parentElement?.closest('main,article,[role="main"]'));
  function isExcluded(el) {
    for (let e = el; e && e !== root.parentElement; e = e.parentElement) if (selfExcluded(e)) return true;
    return false;
  }
  const cache = new WeakMap();
  function visible(el) {
    if (!el || el === document.documentElement) return true;
    if (cache.has(el)) return cache.get(el);
    const style = getComputedStyle(el);
    const result = style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse' && style.opacity !== '0' && visible(el.parentElement);
    cache.set(el, result); return result;
  }
  let text = selected;
  if (!selectionOnly && adLists.size) warnings_pending = 'Annonces voisines (similaires, à proximité…) écartées de la capture.';
  const metadata = {}, jsonLd = [], warnings = [warnings_pending, siteWarning].filter(Boolean);
  let capturedHtml = '';
  if (!selectionOnly) {
    const parts = []; const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let node; let length = 0;
    while ((node = walker.nextNode())) {
      const el = node.parentElement;
      if (!el || isExcluded(el) || !visible(el)) continue;
      const value = node.textContent.replace(/\s+/g, ' ').trim();
      if (value) { parts.push(value); length += value.length + 1; }
      if (length > 120000) { warnings.push('Texte limité à 120 000 caractères.'); break; }
    }
    text = parts.join('\n');
    for (const key of ['description','og:title','og:description','product:price:amount','product:price:currency','og:price:amount','og:price:currency','og:image','twitter:image']) {
      const el = document.querySelector(`meta[property="${key}"],meta[name="${key}"]`);
      if (el?.content) metadata[key] = el.content.slice(0, 3000);
    }
    let budget = 300000;
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { const raw = JSON.stringify(JSON.parse(el.textContent));
        if (raw.length <= budget && jsonLd.length < 30) { jsonLd.push(raw); budget -= raw.length; }
        else if (!warnings.includes('Certains blocs JSON-LD dépassent la limite de capture.')) warnings.push('Certains blocs JSON-LD dépassent la limite de capture.');
      } catch (_) { /* Aucun script n'est exécuté. */ }
    }
    const clone = root.cloneNode(true);
    const originals = [root, ...root.querySelectorAll('*')], copies = [clone, ...clone.querySelectorAll('*')];
    for (let i = originals.length - 1; i >= 0; i--) {
      if (selfExcluded(originals[i]) || !visible(originals[i])) { copies[i].remove(); continue; }
      for (const attr of [...copies[i].attributes]) {
        if (!['class','id','itemprop','itemscope','itemtype','itemid','content','title','data-testid','data-dpe','aria-label'].includes(attr.name)) copies[i].removeAttribute(attr.name);
      }
    }
    capturedHtml = clone.outerHTML;
    if (capturedHtml.length > 1_000_000) { capturedHtml = ''; warnings.push('HTML trop volumineux : le texte et les métadonnées restent inclus.'); }
  }
  // Photo de la fiche : les plus grandes images visibles du contenu principal (aussi en mode sélection).
  const images = [...new Set([...root.querySelectorAll('img')]
    .filter(img => img.complete && img.naturalWidth >= 320 && img.naturalHeight >= 200 && !img.closest('nav') && visible(img))
    .map(img => { const r = img.getBoundingClientRect(); return {src: img.currentSrc || img.src, area: r.width * r.height}; })
    .filter(x => /^https?:\/\//.test(x.src) && x.src.length <= 2048)
    .sort((a, b) => b.area - a.area).map(x => x.src))].slice(0, 3);
  const photos = [...new Set([...(siteData?.ad.images || []), ...images])].slice(0, 5);
  return {url:location.href,title:document.title.slice(0,1000),text:text.slice(0,120000),
    html:capturedHtml,metadata,json_ld:jsonLd,selection_only:selectionOnly,use_gemini:false,capture_warnings:warnings,
    images:photos,site_data:siteData};
})()

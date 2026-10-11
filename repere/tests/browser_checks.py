"""Parcours navigateur dans une base temporaire, sans modifier la base utilisateur.
Exécution : python tests/browser_checks.py (Playwright + Chromium installés).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = 8877
URL = f"http://127.0.0.1:{PORT}"
TOKEN = "repere-browser-test-only"


def run():
    with tempfile.TemporaryDirectory(prefix="repere-browser-") as temp:
        folder=Path(temp)
        env={**os.environ,"DATABASE_URL":"sqlite:///"+str(folder/'test.sqlite3'),"REPERE_DATA_DIR":str(folder),
             "REPERE_TOKEN":TOKEN,"REPERE_PUBLIC_URL":URL}
        screenshots=Path(os.getenv('REPERE_SCREENSHOTS',str(folder/'screenshots')))
        screenshots.mkdir(parents=True,exist_ok=True)
        with (folder/'server.log').open('w+') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(PORT)],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(URL+'/health',timeout=1)
                        break
                    except OSError:
                        time.sleep(.1)
                with sync_playwright() as p:
                    executable=os.getenv('REPERE_CHROMIUM_PATH')
                    browser=p.chromium.launch(executable_path=executable or None,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
                    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
                    errors=[]
                    page.on('pageerror',lambda e: errors.append(str(e)))
                    page.on('dialog',lambda dialog:dialog.accept('Scénario navigateur') if dialog.type=='prompt' else dialog.accept())
                    page.goto(URL)
                    page.screenshot(path=str(screenshots/'connexion.png'),full_page=True)
                    page.locator('#login-token').fill(TOKEN)
                    page.get_by_role('button',name='Ouvrir mon espace').click()
                    expect(page.locator('#shell')).to_be_visible()
                    page.locator('[data-nav=profile]').click()
                    page.locator('[name=income_monthly]').fill('3200')
                    page.locator('[name=savings_eur]').fill('40000')
                    page.locator('[name=reserve_eur]').fill('8000')
                    page.locator('[name=target_monthly]').fill('900')
                    page.get_by_role('button',name='Enregistrer mon budget').click()
                    expect(page.locator('#toast')).to_contain_text('Budget enregistré')
                    page.get_by_role('button',name='Ajouter une annonce',exact=False).first.click()
                    page.get_by_role('button',name='Texte / HTML / fichier').click()
                    page.locator('[name=source_url]').fill('https://example.com/bien/1')
                    page.locator('[name=content]').fill('<main><h1>Appartement ancien · Cergy</h1><p class="price">234 000 €</p><p class="surface">43,3 m²</p><span class="city">Cergy</span><p>2 pièces. DPE : C. Charges : 120 €/mois.</p></main>')
                    page.get_by_role('button',name='Analyser et créer la fiche').click()
                    expect(page.locator('#edit-form [name=price_eur]')).to_have_value('234000')
                    expect(page.locator('#edit-form [name=area_m2]')).to_have_value('43.3')
                    page.locator('#edit-form [name=price_eur]').fill('220000')
                    page.locator('#edit-form [name=notes]').fill('Prévoir une visite côté cour.')
                    page.get_by_role('button',name='Valider la fiche').click()
                    expect(page.locator('#modal')).not_to_be_visible()
                    expect(page.locator('.property-price').first).to_contain_text('220')
                    page.get_by_role('button',name='Ajouter une annonce',exact=False).first.click()
                    page.get_by_role('button',name='Texte / HTML / fichier').click()
                    page.locator('[name=source_url]').fill('https://example.com/bien/2')
                    page.locator('[name=content]').fill('<main><h1>Appartement neuf · Sartrouville</h1><p class="price">258 600 €</p><span class="surface">43 m²</span><span class="city">Sartrouville</span><p>2 pièces. DPE : B. Surface habitable : 43 m². Appartement neuf avec balcon.</p></main>')
                    page.get_by_role('button',name='Analyser et créer la fiche').click()
                    expect(page.locator('#edit-form [name=price_eur]')).to_have_value('258600')
                    page.get_by_role('button',name='Valider la fiche').click()
                    expect(page.locator('#modal')).not_to_be_visible()
                    expect(page.locator('[data-compare]')).to_have_count(2)
                    page.locator('[data-compare]').nth(0).check()
                    page.locator('[data-compare]').nth(1).check()
                    page.locator('[data-nav=compare]').click()
                    expect(page.locator('#compare-body')).to_contain_text('Mensualité maximale')
                    page.screenshot(path=str(screenshots/'comparateur.png'),full_page=True)
                    page.locator('[data-action=simulate]').first.click()
                    expect(page.locator('#finance-form')).to_be_visible()
                    page.get_by_role('button',name='Calculer ma simulation').click()
                    expect(page.locator('#finance-results')).to_contain_text('Vos mensualités dans le temps')
                    page.get_by_role('button',name='Mensualité → apport').click()
                    page.locator('[name=target_monthly]').fill('900')
                    page.get_by_role('button',name='Calculer ma simulation').click()
                    expect(page.locator('#finance-results')).to_contain_text('Apport nécessaire')
                    page.screenshot(path=str(screenshots/'simulation.png'),full_page=True)
                    page.get_by_role('button',name='Enregistrer ce scénario').click()
                    expect(page.locator('#scenarios-section')).to_contain_text('Scénario navigateur')
                    page.locator('[name=target_monthly]').fill('850')
                    expect(page.locator('#finance-results')).to_contain_text('Recalculez')
                    page.locator('[data-action=load-scenario]').click()
                    expect(page.locator('[name=target_monthly]')).to_have_value('900')
                    expect(page.locator('#finance-results')).to_contain_text('Apport nécessaire')
                    page.locator('[data-nav=dashboard]').click()
                    page.screenshot(path=str(screenshots/'dashboard.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(screenshots/'mobile.png'),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.locator('[data-nav=settings]').click()
                    with page.expect_download() as info:
                        page.get_by_role('button',name='Télécharger une sauvegarde').click()
                    downloaded=info.value
                    backup=json.loads(Path(downloaded.path()).read_text())
                    assert len(backup['listings'])==2 and len(backup['scenarios'])==1
                    assert TOKEN not in json.dumps(backup)
                    assert not errors,errors
                    # Le collecteur est exécuté dans le vrai DOM Chromium, avec API
                    # d'extension testée séparément par simulation.
                    fixture='<main><h1>Appartement</h1><div class="price">234 000 €</div><p>Surface habitable : 43 m². Ce logement est en vente avec un balcon.</p><form><input value="secret"></form><p hidden>Texte privé</p></main>'
                    page.set_content(fixture)
                    captured=page.evaluate((ROOT/'extension/capture.js').read_text())
                    assert '234 000' in captured['text'] and 'price' in captured['html']
                    assert 'secret' not in captured['html'] and 'Texte privé' not in captured['text']
                    browser.close()
                    print('Parcours navigateur réussi : connexion, budget, import HTML, correction, comparaison, simulation inverse, scénario, sauvegarde, affichage mobile, capture DOM.')
                    print('Captures :',screenshots)
            except Exception:
                log.flush();log.seek(0)
                print(log.read()[-6000:])
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__=='__main__':
    run()

import json
from pathlib import Path
from unittest.mock import patch
import pytest
from app.schemas import Capture, Facts
from app.parsing.pipeline import pipeline
from app.parsing.gemini import Answer, Evidence, verify
from app.security import public_url, public_addresses
from fastapi import HTTPException

URL = 'https://www.jinka.fr/ad/31888795-b893-4ec9-90f2-e577d7d849ae'
NODE = {'@type':'Apartment','url':URL,'name':'Appartement ancien','floorSize':{'value':43.3,'unitCode':'MTK'},
        'offers':{'@type':'Offer','price':234000,'priceCurrency':'EUR'},'address':{'addressLocality':'Cergy'}}


def test_jsonld_and_tracking_identity():
    r=pipeline(Capture(url=URL+'?alert_id=123',json_ld=[json.dumps(NODE)]))
    assert r['facts']['price_eur']==234000
    assert r['facts']['area_m2']==43.3
    assert r['facts']['city']=='Cergy'
    assert r['candidates']['price_eur'][0]['parser']=='JSON-LD'


def test_microdata_from_html():
    html='''<main><div itemscope itemtype="https://schema.org/Apartment">
    <span itemprop="name">Appartement ancien</span><div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <meta itemprop="priceCurrency" content="EUR"><span itemprop="price" content="234000">234 000 €</span></div>
    <div itemprop="floorSize" itemscope itemtype="https://schema.org/QuantitativeValue"><meta itemprop="unitCode" content="MTK"><span itemprop="value" content="43.3">43,3</span></div>
    <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress"><span itemprop="addressLocality">Cergy</span></div></div></main>'''
    r=pipeline(Capture(url=URL,html=html))
    assert r['facts']['price_eur']==234000
    assert r['facts']['area_m2']==43.3
    assert any('extruct' in c['parser'] for c in r['candidates']['price_eur'])


def test_dom_price_and_generic_surface():
    r=pipeline(Capture(url=URL,html='<main><h1>Appartement</h1><p class="price">234 000 €</p><span class="surface">43,3 m²</span><span class="city">Cergy</span></main>'))
    assert r['facts']['price_eur']==234000
    assert r['facts']['area_m2']==43.3
    assert r['facts']['city']=='Cergy'
    assert pipeline(Capture(html='<main><span class="surface">43,3 m2</span></main>'))['facts']['area_m2']==43.3


def test_text_and_annualization():
    r=pipeline(Capture(text='Ville : Cergy\nPrix de vente\n234 000 €\nSurface habitable : 43,3 m². Charges : 120 €/mois. Taxe foncière : 1000 €.'))
    assert r['facts']['price_eur']==234000
    assert r['facts']['charges_annual']==1440
    assert r['facts']['tax_annual'] is None


def test_metadata_price_without_label_and_no_rental_or_price_per_area():
    r=pipeline(Capture(title='Appartement 43,3 m² - 234 000 €'))
    assert r['facts']['price_eur']==234000
    for title in ['Appartement à louer 43 m² - 1200 €','Appartement à partir de 234 000 €','Appartement 5000 €/m²']:
        assert pipeline(Capture(title=title))['facts']['price_eur'] is None


def test_conflict_keeps_candidates_without_arbitrary_winner():
    r=pipeline(Capture(url=URL,json_ld=[json.dumps(NODE)],text='Prix : 250 000 €'))
    assert r['facts']['price_eur'] is None
    assert {x['value'] for x in r['conflicts']['price_eur']}=={234000,250000}


def test_selection_drops_html_and_metadata():
    r=pipeline(Capture(selection_only=True,text='Prix : 210 000 €',metadata={'og:title':'Appartement 250 000 €'},json_ld=[json.dumps(NODE)],html='<div class="price">260 000 €</div>'))
    assert r['facts']['price_eur']==210000
    assert len(r['candidates']['price_eur'])==1


def test_trafilatura_is_callable_and_identified():
    r=pipeline(Capture(url=URL,html='<html><body><article><h1>Appartement ancien</h1><p>Nous vous proposons un appartement situé au centre-ville. Prix : 234 000 €. Surface habitable : 60 m². Les commerces sont accessibles à pied et le logement dispose d’une terrasse.</p></article></body></html>',parsers=['trafilatura']))
    assert r['facts']['price_eur']==234000
    assert any('Trafilatura' in item['parser'] for item in r['candidates']['price_eur'])


def test_site_rule_can_be_added_without_python_change(tmp_path,monkeypatch):
    cfg=tmp_path/'parsers.json';cfg.write_text(json.dumps({'sites':[{'hosts':['example.com'],'fields':{'price_eur':{'selector':'#prix-propre-au-site','unit':'EUR'}}}]}))
    monkeypatch.setenv('PARSER_RULES_PATH',str(cfg))
    r=pipeline(Capture(url='https://example.com/annonce',html='<main><span id="prix-propre-au-site">234000</span></main>',parsers=['html']))
    assert r['facts']['price_eur']==234000
    assert r['candidates']['price_eur'][0]['parser']=='Règle du site'


def test_invalid_site_selector_preserves_other_extractions(tmp_path,monkeypatch):
    cfg=tmp_path/'parsers.json'
    cfg.write_text(json.dumps({'sites':[{'hosts':['example.com'],'fields':{'price_eur':{'selector':'[malforme','unit':'EUR'}}}]}))
    monkeypatch.setenv('PARSER_RULES_PATH',str(cfg))
    r=pipeline(Capture(url='https://example.com/annonce',html='<main><span class="price">234 000 €</span></main>',parsers=['html']))
    assert r['facts']['price_eur']==234000
    assert any('Règles du site non appliquées' in item for item in r['warnings'])


def test_bad_gemini_quote_and_changed_numeric_value_are_rejected():
    answer=Answer(facts=Facts(price_eur=234000,area_m2=50,city='Paris'),evidence=[
        Evidence(field='price_eur',quote='"price": 234000'),Evidence(field='area_m2',quote='Surface : 43,3 m²'),Evidence(field='city',quote='Paris')])
    found,rejected=verify(answer,'JSON-LD: {"price":234000}. Surface : 43,3 m²')
    assert found[0]['value']==234000
    assert set(rejected)=={'area_m2','city'}


def test_optional_ai_failure_keeps_local_extraction():
    with patch('app.parsing.gemini.extract_ai',side_effect=RuntimeError('Quota')):
        r=pipeline(Capture(text='Prix : 234 000 €',use_gemini=True))
    assert r['facts']['price_eur']==234000
    assert any('Quota' in w for w in r['warnings'])


def test_challenge_is_not_sent_to_gemini():
    with patch('app.parsing.gemini.extract_ai',side_effect=AssertionError('AI')):
        r=pipeline(Capture(text='Please verify that you are human',use_gemini=True))
    assert r['facts']['price_eur'] is None
    assert r['steps'][0]['error']


def test_agency_city_and_credit_simulator_are_not_listing_facts():
    r=pipeline(Capture(html='<main><h1>Appartement</h1><section itemscope itemtype="https://schema.org/RealEstateAgent"><span itemprop="addressLocality">Paris</span></section><div>Simulation de crédit :<span class="price">1500 €</span> par mois</div></main>',parsers=['html']))
    assert r['facts']['city'] is None
    assert r['facts']['price_eur'] is None


@pytest.mark.parametrize('url',['http://localhost/a','http://127.0.0.1/a','http://192.168.1.2/a','http://[::1]/','file:///etc/passwd','https://user:password@example.com/a'])
def test_private_urls_are_refused(url):
    with pytest.raises(HTTPException):public_url(url)


def test_dns_resolving_to_private_address_is_refused():
    with patch('app.security.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',80))]):
        with pytest.raises(HTTPException):public_addresses('example.com',80)

from unittest.mock import patch
from app.db import engine
from app.schemas import Capture


def test_capture_to_listing_validation_and_reimport_keep_edits(client, capture):
    first = client.post('/capture', json=capture).json()['listing']
    assert first['facts']['price_eur'] == 234000
    assert first['facts']['charges_annual'] == 1440
    edited = client.patch('/api/listings/'+first['id'], json={
        'revision':first['revision'], 'title':'Ma sélection', 'facts':{'price_eur':220000}, 'notes':'Visite mardi', 'reviewed':True}).json()
    assert edited['facts']['price_eur'] == 220000
    capture['text'] = capture['text'].replace('234 000','230 000').replace('43,3','44,3')
    again = client.post('/capture', json=capture).json()['listing']
    assert again['id'] == first['id']
    assert again['title'] == 'Ma sélection'
    assert again['facts']['price_eur'] == 220000
    assert again['facts']['area_m2'] == 44.3
    assert 'title' not in again['facts']
    assert again['notes'] == 'Visite mardi'
    assert again['reviewed'] is False
    unlocked = client.patch('/api/listings/'+first['id'], json={'unlock_fields':['price_eur']}).json()
    assert unlocked['facts']['price_eur'] == 230000


def test_conflict_revision_rejected(client, capture):
    row = client.post('/capture', json=capture).json()['listing']
    client.patch('/api/listings/'+row['id'],json={'revision':row['revision'],'favorite':True})
    assert client.patch('/api/listings/'+row['id'],json={'revision':row['revision'],'notes':'Ancienne version'}).status_code == 409


def test_preview_does_not_persist_and_does_not_fetch(client, capture):
    with patch('app.main.fetch_html',side_effect=AssertionError('Network')):
        r=client.post('/api/preview',json=capture)
    assert r.status_code == 200
    assert client.get('/api/listings').json() == []


def test_auth_and_origin_for_all_personal_data(client, capture):
    for route in ['/api/listings','/api/profile','/api/backup','/api/scenarios']:
        assert client.get(route,headers={'X-Repere-Token':''}).status_code == 401
    assert client.post('/capture',json=capture,headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/capture',json=capture,headers={'Origin':'chrome-extension://'+'a'*32}).status_code == 200
    assert client.get('/health',headers={'X-Repere-Token':''}).status_code == 200


def test_url_fetch_is_explicit(client, capture):
    with patch('app.main.fetch_html',return_value=(capture['url'],'<main><h1>Appartement 60 m²</h1><div class="price">234 000 €</div></main>')) as fetch:
        r=client.post('/api/import',json={'capture':{'url':capture['url']},'fetch_url':True})
        assert r.status_code == 200,r.text
        assert fetch.call_count == 1
        assert r.json()['facts']['price_eur'] == 234000


def test_backup_restore_idempotent_and_no_tokens(client,capture):
    row=client.post('/capture',json=capture).json()['listing']
    client.put('/api/profile',json={'income_monthly':3000,'savings_eur':40000})
    client.post('/api/scenarios',json={'name':'Mon essai','listing_id':row['id'],'inputs':{'price_eur':234000,'down_payment':40000}})
    backup=client.get('/api/backup').json()
    assert 'repere-test-token' not in str(backup)
    assert client.post('/api/restore',json=backup).json()['skipped']==1
    client.delete('/api/listings/'+row['id'])
    out=client.post('/api/restore',json=backup)
    assert out.status_code == 200,out.text
    assert out.json()['added']==1
    assert len(client.get('/api/listings').json())==1
    assert client.get('/api/listings/'+row['id']+'/source').status_code==200


def test_invalid_backup_rolls_back_entire_transaction(client,capture):
    row=client.post('/capture',json=capture).json()['listing']
    backup=client.get('/api/backup').json()
    client.delete('/api/listings/'+row['id'])
    backup['listings'].append({'id':'invalid'})
    assert client.post('/api/restore',json=backup).status_code==422
    assert client.get('/api/listings').json()==[]


def test_schema_validation_and_nan_are_not_500(client):
    assert client.post('/api/listings',json={'facts':{'price_eur':-1}}).status_code == 422
    assert client.post('/api/simulate',json={'price_eur':100000,'down_payment':200000}).status_code==422


def test_profile_and_scenarios_survive_new_connections(client,capture):
    client.put('/api/profile',json={'income_monthly':3200})
    assert client.get('/api/profile').json()['income_monthly']==3200
    saved=client.post('/api/scenarios',json={'name':'Référence','inputs':{'price_eur':180000}})
    assert saved.status_code==200
    engine.dispose()
    assert client.get('/api/profile').json()['income_monthly']==3200
    assert len(client.get('/api/scenarios').json())==1


def test_reanalyze_uses_saved_capture_without_refetch(client,capture):
    row=client.post('/capture',json=capture).json()['listing']
    with patch('app.main.fetch_html',side_effect=AssertionError('Network')):
        result=client.post('/api/listings/'+row['id']+'/reanalyze',json={'use_gemini':False})
    assert result.status_code==200
    assert result.json()['facts']['price_eur']==234000


def test_saved_html_is_never_executed_or_left_with_form_values(client):
    r=client.post('/api/import',json={'capture':{'html':'<main><h1>Appartement</h1><div class="price" onclick="alert(1)">234 000 €</div><form><input value="secret"></form><script>alert(1)</script></main>'}})
    assert r.status_code==200,r.text
    source=client.get('/api/listings/'+r.json()['id']+'/source').json()['capture']['html']
    assert 'secret' not in source and 'onclick' not in source and 'alert(1)' not in source

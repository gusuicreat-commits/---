"""底漆/面漆简称只路由到自己的检验项，纠错不能交换漆种。"""
from datetime import date

import pytest

from app.voice import parser


@pytest.mark.parametrize('text,expected', [
    ('底漆九十八、九十七、九十六', {'primer_thickness_ratio': '98,97,96'}),
    ('面漆九十九', {'total_thickness_ratio': '99'}),
    ('底漆98面漆99附着力1', {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99', 'adhesion_loss_ratio': '1'}),
    ('底漆，98，97，96，面漆，99', {'primer_thickness_ratio': '98,97,96', 'total_thickness_ratio': '99'}),
    ('第七，九十八，九十七，九十六，面漆，九十九。', {'total_thickness_ratio': '99'}),
    ('底漆，面漆99', {'total_thickness_ratio': '99'}),
    ('面漆99，底漆98、97、96', {'total_thickness_ratio': '99', 'primer_thickness_ratio': '98,97,96'}),
    ('底漆厚度98，面漆厚度99', {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99'}),
    ('底漆厚度达标率98%，漆膜总厚度达标率99%', {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99'}),
    ('底气98', {}), ('面气99', {}), ('第七98', {}), ('勉其99', {}),
    ('厚度98', {}),
    ('底漆98，不对，面漆99', {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99'}),
])
def test_distinct_paint_aliases(text, expected):
    assert parser.parse_text(text, 'travel_paint', date(2026, 11, 10))['fields'] == expected


@pytest.mark.parametrize('mapping', [
    {'底漆': '面漆', '面漆': '底漆'},
    {'底': '面'}, {'面': '底'},
    {'底漆厚度': '面漆厚度'},
])
def test_existing_learned_corrections_cannot_swap_paint(monkeypatch, mapping):
    monkeypatch.setattr(parser, '_learned_corrections', mapping)
    result = parser.parse_text('底漆厚度98，面漆99', 'travel_paint', date(2026, 11, 10))
    assert result['fields'] == {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99'}


def test_corrections_preserving_paint_name_still_work(monkeypatch):
    monkeypatch.setattr(parser, '_learned_corrections', {'底漆后度': '底漆厚度'})
    assert parser.parse_text('底漆后度98', 'travel_paint', date(2026, 11, 10))['fields'] == {'primer_thickness_ratio': '98'}


@pytest.mark.parametrize('wrong,right', [('底漆', '面漆'), ('面漆', '底漆'), ('底漆厚度', '面漆厚度')])
def test_global_swapping_corrections_are_rejected(client, wrong, right):
    response = client.post('/api/corrections', json={'wrong': wrong, 'right': right})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'BAD_CORRECTION'


def test_uncertain_paint_goes_to_review_even_if_other_field_is_recognized(client):
    response = client.post('/api/voice/parse-text', json={
        'text': '第七，九十八，九十七，面漆，九十九', 'form': 'travel_paint'})
    parsed = response.json()
    assert parsed['fields'] == {'total_thickness_ratio': '99'}
    assert parsed['rejected'][0]['field'] == 'paint_kind'
    assert client.get(f"/api/voice/logs/{parsed['voice_id']}").json()['status'] == 'review'

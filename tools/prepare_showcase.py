"""按演示工艺卡补规则和样例合同；默认只预览，--apply 才写入。

示例：.venv/bin/python tools/prepare_showcase.py --apply
只处理 config/showcase.json 中代码、样例名同时匹配的合同；不删除历史。
"""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('contract', 'drum_param', 'travel_drive_param', 'control_cabinet_param',
          'travel_paint_param', 'voice_log', 'learned_correction', 'demo_clip')


def snapshot(path, excluded):
    result = {}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for table in TABLES:
            rows = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
            if table not in ('learned_correction', 'demo_clip'):
                rows = [row for row in rows if row.get('ht_code') not in excluded]
            result[table] = rows
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return digest


def same_values(latest, payload, meta):
    if latest is None:
        return False
    for field in meta['fields']:
        old, value = latest.get(field['name']), payload.get(field['name'])
        if field['type'] == 'number':
            if old is None or value is None or Decimal(str(old)) != Decimal(str(value)):
                return False
        elif old != value:
            return False
    return True


def prepare(client, spec, fields, current_rules, contracts):
    assert spec['sample_only'] is True
    keys = {(table, f['name']) for table, meta in fields.items() for f in meta['fields'] if f['validate']}
    profile = {(r['table'], r['field']): r for r in spec['rules']}
    assert keys == set(profile), '工艺卡与当前校验字段不一致'
    rule_values, changes = {}, []
    for rule in current_rules['items']:
        sample = profile[rule['table_name'], rule['name']]
        lower = rule['min'] if rule['min'] is not None else sample['min']
        upper = rule['max'] if rule['max'] is not None else sample['max']
        assert Decimal(lower) <= Decimal(upper), '补齐后规则上下限冲突'
        rule_values[rule['table_name'], rule['name']] = (Decimal(lower), Decimal(upper))
        if rule['min'] is None or rule['max'] is None:
            changes.append({'id': rule['id'], 'label': rule['label'], 'min': lower, 'max': upper, 'basis': sample['basis']})
    existing = {row['ht_code']: row for row in contracts}
    kinds = {meta['kind']: (table, meta) for table, meta in fields.items()}
    plan = []
    for case in spec['scenarios']:
        code = case['code']
        assert '样例' in case['name'], '禁止准备未标样例的合同'
        if code in existing:
            assert existing[code]['name'] == case['name'], '合同号被其他名称使用：' + code
            detail = client.get('/contracts/' + code).raise_for_status().json()
        else:
            detail = {'latest': {kind: None for kind in kinds}}
        for kind in case.get('must_be_missing', []):
            assert detail['latest'][kind] is None, f'{code} 的 {kind} 已有数据，不能为制造未录全而删除'
        payloads, writes = {}, []
        for kind in case.get('include', list(kinds)):
            table, meta = kinds[kind]
            payload = {**spec['normal'][kind], **case.get('overrides', {}).get(kind, {})}
            payloads[kind] = payload
            if not same_values(detail['latest'][kind], payload, meta):
                writes.append({'kind': kind, 'values': payload})
        missing = any(kind not in payloads and detail['latest'][kind] is None for kind in kinds)
        bad = any(not (rule_values[table, f['name']][0] <= Decimal(str(payload[f['name']])) <= rule_values[table, f['name']][1])
                  for kind, payload in payloads.items() for table, meta in [kinds[kind]] for f in meta['fields'] if f['validate'])
        expected = '未录全' if missing else '不合格' if bad else '合格'
        assert expected == case['status'], f'{code} 的样例与当前规则冲突：预期 {case["status"]}，实际 {expected}'
        plan.append({**case, 'create': code not in existing, 'writes': writes})
    return changes, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='https://localhost:8443')
    parser.add_argument('--db-path', type=Path, default=ROOT / 'data/e2e.sqlite3')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/showcase-report.json')
    args = parser.parse_args()
    spec = json.loads((ROOT / 'config/showcase.json').read_text())
    codes = {case['code'] for case in spec['scenarios']}
    # 仅本机开发服务使用自签名 HTTPS。
    local_https = args.base.startswith(('https://localhost:', 'https://127.0.0.1:'))
    with httpx.Client(base_url=args.base.rstrip('/') + '/api', verify=not local_https, timeout=30) as client:
        fields = client.get('/fields').raise_for_status().json()
        rules = client.get('/rules').raise_for_status().json()
        contracts = client.get('/contracts').raise_for_status().json()['items']
        with sqlite3.connect(args.db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db_contracts = {(r[0], r[1], r[2]) for r in db.execute('SELECT id,ht_code,name FROM contract')}
        assert db_contracts == {(r['id'], r['ht_code'], r['name']) for r in contracts}, '备份库与目标 API 不一致'
        changes, plan = prepare(client, spec, fields, rules, contracts)
        report = {'profile': spec['name'], 'sample_only': True, 'mode': 'apply' if args.apply else 'preview',
                  'rule_changes': changes, 'scenarios': plan,
                  'planned_distribution': dict(Counter(case['status'] for case in plan))}
        if args.apply:
            before = snapshot(args.db_path, codes)
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S-%f')
            backup = args.db_path.parent / 'backup' / f'showcase-before-{stamp}.sqlite3'
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(args.db_path.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            report['backup'] = str(backup)
            for rule in changes:
                client.put(f'/rules/{rule["id"]}?source=screen', json={'min': rule['min'], 'max': rule['max']}).raise_for_status()
            for case in plan:
                code = case['code']
                if case['create']:
                    client.post('/contracts?source=screen', json={'ht_code': code, 'name': case['name'],
                                                                'date': f'{code[:4]}-{code[4:6]}-{code[6:8]}'}).raise_for_status()
                for write in case['writes']:
                    response = client.post(f'/contracts/{code}/records/{write["kind"]}?source=screen', json=write['values']).raise_for_status().json()
                    assert response['validation'] is not None, f'{code} 保存后未完成校验'
                client.post(f'/contracts/{code}/revalidate').raise_for_status()
            items = client.get('/contracts').raise_for_status().json()['items']
            current = {item['ht_code']: item for item in items}
            assert all(current[case['code']]['status'] == case['status'] for case in plan)
            progress = client.get('/rules').raise_for_status().json()
            assert progress['configured'] == progress['total'] == len(spec['rules'])
            after = snapshot(args.db_path, codes)
            assert before == after, '非本批样例的数据发生变化，请检查是否有同时操作'
            report.update(actual_distribution=dict(Counter(current[case['code']]['status'] for case in plan)),
                          all_contract_distribution=dict(Counter(item['status'] for item in items)),
                          configured_rules=progress['configured'], protected_data_unchanged=True,
                          protected_data_hash=after)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: report[key] for key in ('mode', 'planned_distribution', 'actual_distribution',
                         'all_contract_distribution', 'configured_rules', 'protected_data_unchanged', 'backup') if key in report}, ensure_ascii=False, indent=2))
        print('待补规则：', len(changes), '；待追加参数记录：', sum(len(case['writes']) for case in plan))
        print('记录：', args.output)


if __name__ == '__main__':
    main()

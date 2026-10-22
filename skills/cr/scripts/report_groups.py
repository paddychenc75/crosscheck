"""仅合并已核实发现的展示；原始发现、编号和历史记录保持独立。"""
import copy
import json
import os

import crlib


def build(final, plan_path):
    display = copy.deepcopy(final)
    for fs in display.get('groups', {}).values():
        for f in fs:
            for key in ('related_findings', 'display_members', 'display_reason'):
                f.pop(key, None)
    display['display_groups'] = []
    if not os.path.isfile(plan_path):
        return display
    try:
        with open(plan_path, encoding='utf-8') as f:
            plan = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError('展示分组不是有效 JSON') from e
    if not isinstance(plan, list):
        raise ValueError('展示分组应为数组')
    records = [f for fs in display.get('groups', {}).values() for f in fs]
    by_id = {f.get('id'): f for f in records}
    if len(by_id) != len(records) or any(not x for x in by_id):
        raise ValueError('最终发现缺少唯一编号')
    used, replacements, hidden = set(), {}, set()
    for row in plan:
        if not isinstance(row, dict):
            raise ValueError('展示分组条目应为对象')
        members, primary = row.get('members'), row.get('primary')
        if (not isinstance(members, list) or len(members) < 2
                or any(not isinstance(x, str) for x in members)
                or len(set(members)) != len(members) or primary not in members
                or not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            raise ValueError('每组须有主编号、至少两个不同成员及同一失败事实的理由')
        if any(x not in by_id or x in used for x in members):
            raise ValueError('分组含不存在、未展示或重复使用的编号')
        fs = [by_id[x] for x in members]
        if len({f.get('file') for f in fs}) != 1:
            raise ValueError('不同文件的发现不在展示阶段合并')
        try:
            scores = [float(f['confidence']) for f in fs]
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError('只能合并有核实分数的发现') from e
        if any(not 80 <= score <= 100 or not score.is_integer() for score in scores):
            raise ValueError('只能合并已通过80分门槛的发现')
        categories = {f.get('category') for f in fs}
        if 'simplify' in categories and len(categories) > 1:
            raise ValueError('简化与缺陷/规范的成立条件不同，不能合并展示')
        entry = copy.deepcopy(by_id[primary])
        entry['related_findings'] = [copy.deepcopy(by_id[x]) for x in members if x != primary]
        entry['display_members'] = [{'id': f['id'], 'no': f.get('no'), 'category': f.get('category')} for f in fs]
        entry['display_reason'] = row['reason']
        entry['confidence'] = int(min(scores))
        entry['severity'] = min((f.get('severity', 'minor') for f in fs), key=lambda s: crlib.SEV_ORDER.get(s, 3))
        replacements[primary] = entry
        hidden.update(x for x in members if x != primary)
        used.update(members)
        display['display_groups'].append(copy.deepcopy(row))
    for group, fs in display.get('groups', {}).items():
        display['groups'][group] = [replacements.get(f['id'], f) for f in fs if f['id'] not in hidden]
    return display

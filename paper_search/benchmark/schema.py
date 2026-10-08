from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = 'scm-multiselect-v1'
CLASSIFIED_SCHEMA_VERSION = 'scm-multiselect-v2'
TAXONOMY_VERSION = 'scm-business-domain-v1'
DOMAIN_NAMES = {
    'strategy_network_design': '战略与供应网络设计',
    'planning_inventory_replenishment': '计划、库存与补货',
    'sourcing_procurement_supplier_management': '采购、寻源与供应商管理',
    'production_capacity_process_operations': '生产、产能与过程运营',
    'logistics_distribution_fulfillment': '物流、配送与履约',
}
LABELS = ['A', 'B', 'C', 'D']
SYSTEM_PROMPT = ('你正在回答一道独立的供应链决策多选题。仅根据本题条件作答。\n'
                 '最终回答必须且只能是一个 JSON 对象。下面的代码块仅用于展示格式：\n\n'
                 '```json\n{"answer":["A","C"]}\n```\n\n'
                 '示例仅说明格式，不代表本题答案。\n'
                 'answer 数组包含 1–3 个不重复的大写选项字母，仅限 A、B、C、D，按字母顺序排列。\n'
                 '只能包含 answer 字段。\n'
                 '最终回答只输出 JSON 对象本身，不包含代码块标记（```）、解释、题号或其他字段。')

DISCUSSION_SYSTEM_PROMPT = '''你正在回答一道独立的供应链决策多选题。仅根据本题条件作答。

请根据题目给定的条件，分别简要分析 A、B、C、D 四个选项，再给出最终答案。

每个选项说明其成立或不成立的关键依据，必要时给出简短计算。
分析长度以解释清楚为准，避免重复题干或展开无关背景。
不要引入题目未给出的假设。

按以下结构输出：
A：……
B：……
C：……
D：……

最后输出唯一的答案 JSON：
{"answer":["A","C"]}

answer 是唯一字段，数组包含 1–3 个不重复的大写选项字母，
仅限 A、B、C、D，按字母顺序排列。
示例选项仅说明格式，不代表本题答案。
分析部分不要输出候选答案 JSON，最终答案后不要输出任何内容。'''


def strict_json(text):
    def pairs(values):
        out = {}
        for k, v in values:
            if k in out:
                raise ValueError('duplicate JSON key: ' + k)
            out[k] = v
        return out
    def invalid(value):
        raise ValueError('nonfinite JSON value: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def read_json(path):
    return strict_json(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def check_answer(answer):
    if (not isinstance(answer, list) or not 1 <= len(answer) <= 3
            or any(type(v) is not str or v not in LABELS for v in answer)
            or len(set(answer)) != len(answer)):
        raise ValueError('answer must contain 1–3 distinct uppercase A/B/C/D labels')
    return sorted(answer)


def render(item):
    return item['stem'].strip() + '\n\n' + '\n\n'.join(
        label + '. ' + item['options'][label] for label in LABELS)


def check_classification(value):
    if not isinstance(value, dict) or set(value) != {'taxonomy_version', 'categories', 'rationale'}:
        raise ValueError('invalid classification fields')
    cats = value['categories']
    if (value['taxonomy_version'] != TAXONOMY_VERSION or not isinstance(cats, list)
            or not 1 <= len(cats) <= 2 or any(type(c) is not str or c not in DOMAIN_NAMES for c in cats)
            or len(set(cats)) != len(cats) or not isinstance(value['rationale'], str)
            or not value['rationale'].strip()):
        raise ValueError('classification requires 1–2 distinct domains and a rationale')
    return value


def validate(dataset, key=None):
    if not isinstance(dataset,dict):raise ValueError('dataset must be an object')
    if set(dataset) != {'schema_version', 'dataset_id', 'purpose', 'items'}:
        raise ValueError('public dataset top-level fields differ from schema')
    if dataset['schema_version'] not in {SCHEMA_VERSION, CLASSIFIED_SCHEMA_VERSION} or dataset['purpose'] != 'development_pilot':
        raise ValueError('unknown version/purpose')
    if not isinstance(dataset['dataset_id'], str) or not dataset['dataset_id']:
        raise ValueError('missing dataset_id')
    if not isinstance(dataset['items'], list):
        raise ValueError('items must be an array')
    ids = []
    for item in dataset['items']:
        if not isinstance(item,dict):raise ValueError('item must be an object')
        if set(item) != {'id', 'paper_id', 'type', 'stem', 'options'}:
            raise ValueError('public item contains unknown/missing fields (possibly private data)')
        if not isinstance(item['id'],str) or not re.fullmatch(r'P\d+-Q\d+', item['id']) or item['paper_id'] != item['id'].split('-')[0]:
            raise ValueError('invalid item identity')
        if item['type'] != 'multiple_select' or not isinstance(item['stem'], str) or not item['stem'].strip():
            raise ValueError('invalid item type/stem')
        if not isinstance(item['options'], dict) or set(item['options']) != set(LABELS):
            raise ValueError('four options required')
        if any(not isinstance(v, str) or not v.strip() for v in item['options'].values()):
            raise ValueError('empty option')
        ids.append(item['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate item IDs')
    if key is not None:
        if not isinstance(key,dict):raise ValueError('answer key must be an object')
        if set(key) != {'schema_version', 'dataset_id', 'items'} or key['schema_version'] != dataset['schema_version'] or key['dataset_id'] != dataset['dataset_id']:
            raise ValueError('answer key does not match dataset')
        if not isinstance(key['items'], list):
            raise ValueError('invalid key items')
        keys = []
        for item in key['items']:
            if not isinstance(item,dict):raise ValueError('key item must be an object')
            fields = {'id', 'answer', 'verification_status', 'evidence_markdown', 'provenance'}
            if dataset['schema_version'] == CLASSIFIED_SCHEMA_VERSION:
                fields.add('classification')
            if set(item) != fields:
                raise ValueError('invalid private item fields')
            if 'classification' in fields:
                check_classification(item['classification'])
            check_answer(item['answer'])
            if item['verification_status'] != 'READY' or not isinstance(item['evidence_markdown'], str) or not item['evidence_markdown'].strip() or not isinstance(item['provenance'], dict):
                raise ValueError('invalid private verification record')
            keys.append(item['id'])
        if len(set(keys)) != len(keys) or set(keys) != set(ids):
            raise ValueError('public/private IDs differ')
    return ids


def markdown_blocks(text):
    parts = re.split(r'^##\s+\[?(P\d+-Q\d+)\]?[^\n]*\n', text, flags=re.M)
    rows = [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]
    if len({i for i, _ in rows}) != len(rows):
        raise ValueError('duplicate Markdown IDs')
    return rows


def from_markdown(public, private, dataset_id, provenance=None):
    records = dict(markdown_blocks(private))
    items, answers = [], []
    for item_id, body in markdown_blocks(public):
        matches = list(re.finditer(r'^([A-D])\.\s+', body, re.M))
        if [m.group(1) for m in matches] != LABELS:
            raise ValueError(item_id + ': options not unambiguously A–D')
        options = {m.group(1): body[m.end():matches[n+1].start() if n+1 < 4 else len(body)].strip()
                   for n, m in enumerate(matches)}
        stem = body[:matches[0].start()].strip()
        if re.search(r'原论文|本文件共同|共同条件|Verified Answer|Final Status|Source Anchors|Question Behavior|Business Classification|Reasoning Chain|Transformation and Invariants|Final Option Evidence|Card ID|Mechanism Card|PDF\s*(?:页|PAGE)|(?:Figure|Proposition|Lemma|Theorem)\s*\d|P\d+-M\d', body, re.I):
            raise ValueError(item_id + ': source/private leakage')
        record = records.get(item_id, '')
        match = re.search(r'Verified Answer\s*[:：]\s*(\[[^\n]*?\])', record)
        if not match or not re.search(r'Final Status\s*[:：=]\s*READY\b', record):
            raise ValueError(item_id + ': missing READY oracle')
        answer = check_answer(strict_json(match.group(1)))
        items.append(dict(id=item_id, paper_id=item_id.split('-')[0], type='multiple_select', stem=stem, options=options))
        answers.append(dict(id=item_id, answer=answer, verification_status='READY',
                            evidence_markdown=record, provenance=(provenance or {}).get(item_id, {})))
        classification = re.search(r'^Business Classification\s*[:：]\s*(\{[^\n]*\})\s*$', record, re.M)
        if classification:
            answers[-1]['classification'] = check_classification(strict_json(classification.group(1)))
    classified = ['classification' in item for item in answers]
    if any(classified) and not all(classified):
        raise ValueError('partial classification: all exported items must be classified')
    version = CLASSIFIED_SCHEMA_VERSION if classified and all(classified) else SCHEMA_VERSION
    dataset = dict(schema_version=version, dataset_id=dataset_id, purpose='development_pilot', items=items)
    key = dict(schema_version=version, dataset_id=dataset_id, items=answers)
    validate(dataset, key)
    return dataset, key

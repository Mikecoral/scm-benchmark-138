from __future__ import annotations

from collections import Counter
import csv
import html
import json
import re
from pathlib import Path
from paper_search.benchmark.schema import check_answer, strict_json, write_json


ANSWER_OBJECT = re.compile(r'\{\s*"answer"\s*:\s*\[\s*"[A-D]"(?:\s*,\s*"[A-D]"){0,2}\s*\]\s*\}')
OPTION_SECTION = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]+)?(?:[-*+][ \t]+|\d+[.)、][ \t]+)?'
    r'(?:\*\*|__)?(?:选项[ \t]*)?([A-D])(?:\*\*|__)?[ \t]*'
    r'(?:[:：.．、)）](?:\*\*|__)?[ \t]*|$)', re.M)
FINAL_ANSWER_HEADING = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?'
    r'(?:最终答案|答案|final[ \t]+answer)(?:\*\*|__)?[ \t]*[:：]?'
    r'(?:\*\*|__)?[ \t]*$', re.M | re.I)
ANSWER_FENCE_START = re.compile(r'```(?:json)?[ \t]*$', re.I)


def extract_answer_json(content):
    # Locate the answer itself so mathematical brackets in prose do not affect
    # its boundaries. Reject candidates contained in another valid JSON value.
    if len(re.findall(r'"answer"\s*:', content)) != 1:
        return None
    candidates=list(ANSWER_OBJECT.finditer(content))
    if len(candidates)!=1:return None
    match=candidates[0];candidate=match.group()
    decoder=json.JSONDecoder()
    for outer in re.finditer(r'[\{\[]',content[:match.start()]):
        try:_,end=decoder.raw_decode(content,outer.start())
        except ValueError:continue
        if end>=match.end():return None
    try:check_answer(strict_json(candidate)['answer'])
    except (ValueError,TypeError,KeyError):return None
    fenced=re.fullmatch(r'\s*```(?:json)?\s*('+ANSWER_OBJECT.pattern+r')\s*```\s*',content,re.I)
    return candidate, 'code_fence' if fenced else 'unique_json_object'


def tolerant_score(record,gold):
    result=score(record,gold)
    if result['status'] in {'correct','incorrect'}:
        return dict(result,extraction_method='strict_json')
    if result['status']!='invalid_json':
        return dict(result,extraction_method=None)
    extracted=extract_answer_json(record['content'])
    if extracted is None:return dict(result,extraction_method=None)
    candidate,method=extracted
    return dict(score(dict(record,content=candidate),gold),extraction_method=method)


def grade(record,gold,protocol='answer-json-v1'):
    if protocol not in {'answer-json-v1','discussion-answer-json-v1','discussion-answer-json-v2'}:raise ValueError('unknown output protocol')
    result=tolerant_score(record,gold)
    return {k:result[k] for k in ('status','predicted_answer','correct')}


def discussion_format_compliant(record, require_options=False):
    if not record or record.get('finish_reason')!='stop' or record.get('error'):return False
    content=record.get('content') or ''
    extracted=extract_answer_json(content)
    if extracted is None:return False
    candidate,_=extracted;position=content.find(candidate)
    prefix=content[:position].strip();suffix=content[position+len(candidate):].strip()
    fenced=ANSWER_FENCE_START.search(prefix)
    if fenced:
        if suffix!='```':return False
        prefix=prefix[:fenced.start()].strip()
    elif suffix:return False
    answer=strict_json(candidate)['answer']
    if require_options:
        # Check ordered, nonempty A–D sections; this does not assess their reasoning.
        # A standalone final-answer heading cannot count as D's analysis.
        heading=list(FINAL_ANSWER_HEADING.finditer(prefix))
        if heading and not prefix[heading[-1].end():].strip():
            prefix=prefix[:heading[-1].start()].strip()
        sections=list(OPTION_SECTION.finditer(prefix))
        if [m.group(1) for m in sections]!=list('ABCD'):return False
        for i,section in enumerate(sections):
            end=sections[i+1].start() if i+1<len(sections) else len(prefix)
            body=prefix[section.end():end]
            if not re.search(r'[\w]',body):return False
    return bool(prefix) and answer==sorted(answer)


def score(record, gold):
    if record is None:
        return dict(status='missing', predicted_answer=None, correct=False)
    if record.get('error'):
        return dict(status='api_error', predicted_answer=None, correct=False)
    finish = record.get('finish_reason')
    if finish == 'length':
        return dict(status='truncated', predicted_answer=None, correct=False)
    if finish != 'stop':
        return dict(status='non_answer_finish', predicted_answer=None, correct=False)
    content = record.get('content')
    if not isinstance(content, str) or not content.strip():
        return dict(status='empty', predicted_answer=None, correct=False)
    try:
        value = strict_json(content)
    except (ValueError, TypeError):
        return dict(status='invalid_json', predicted_answer=None, correct=False)
    try:
        if not isinstance(value, dict) or set(value) != {'answer'}:
            raise ValueError('output must contain only answer')
        predicted = check_answer(value['answer'])
    except (ValueError, TypeError):
        return dict(status='invalid_answer', predicted_answer=None, correct=False)
    correct = predicted == sorted(gold)
    return dict(status='correct' if correct else 'incorrect', predicted_answer=predicted, correct=correct)


def summarize(items):
    counts = Counter(i['status'] for i in items)
    total = len(items)
    valid = counts['correct'] + counts['incorrect']
    return dict(total=total, counts=dict(counts), correct=counts['correct'], valid_answers=valid,
                accuracy_all=counts['correct']/total if total else None,
                accuracy_valid=counts['correct']/valid if valid else None,
                completion_rate=valid/total if total else None)


def build_report(dataset, key, records, profile_name, protocol='answer-json-v1'):
    gold = {i['id']: i for i in key['items']}
    items = []
    for item in dataset['items']:
        record = records.get(item['id'])
        strict=score(record,gold[item['id']]['answer'])
        result = grade(record, gold[item['id']]['answer'],protocol)
        tolerant=tolerant_score(record,gold[item['id']]['answer'])
        items.append(dict(id=item['id'], paper_id=item['paper_id'],
                          expected_answer=gold[item['id']]['answer'], **result,
                          output_format_compliant=(discussion_format_compliant(record,require_options=protocol=='discussion-answer-json-v2') if protocol in {'discussion-answer-json-v1','discussion-answer-json-v2'}
                                                   else strict['status'] in {'correct','incorrect'}),
                          extraction_method=tolerant['extraction_method'],
                          content=(record or {}).get('content'),
                          reasoning_content=(record or {}).get('reasoning_content'),
                          finish_reason=(record or {}).get('finish_reason'),
                          actual_model=(record or {}).get('actual_model'),
                          error=(record or {}).get('error'),
                          latency_seconds=(record or {}).get('elapsed_seconds'),
                          attempts=(record or {}).get('attempt', 0),
                          usage=(record or {}).get('usage')))
    return dict(dataset_id=dataset['dataset_id'], profile=profile_name,output_protocol=protocol,
                grading_protocol='answer-extraction-v2',
                scoring='exact option-set match; no partial credit; unique final-content JSON extraction',
                note='accuracy_all includes missing/API/format/truncation failures; accuracy_valid only uses valid final answers. Development pilot, not held-out difficulty certification.',
                summary=summarize(items),
                by_paper={pid:summarize([i for i in items if i['paper_id']==pid]) for pid in sorted({i['paper_id'] for i in items})},
                items=items)


def write_report(out, report, dataset):
    out = Path(out)
    write_json(out/'report.json', report)
    fields=['id','paper_id','status','predicted_answer','expected_answer','latency_seconds','attempts','finish_reason','actual_model','error',
            'extraction_method','output_format_compliant']
    with (out/'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for item in report['items']:
            row={k:item[k] for k in fields}
            for k in ['predicted_answer','expected_answer']:
                row[k]=','.join(row[k] or [])
            # Avoid spreadsheet formula execution on externally supplied fields.
            row={k:('\''+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()}
            writer.writerow(row)
    def percent(v):return 'N/A' if v is None else f'{v:.1%}'
    s=report['summary']
    md=f"# Evaluation: {report['profile']}\n\n全量准确率：{percent(s['accuracy_all'])}；有效答案准确率：{percent(s['accuracy_valid'])}；有效答案率：{percent(s['completion_rate'])}。\n\n{report['note']}\n\n| Paper | Total | Correct | Accuracy all | Valid answers |\n|---|---:|---:|---:|---:|\n"
    metric_note='主指标按唯一合法答案 JSON 提取评分；代码围栏或前后说明不影响答案正确性。'
    md=md.replace('\n\n'+report['note'], '\n\n'+metric_note+'\n\n'+report['note'])
    for pid,v in report['by_paper'].items():md+=f"| {pid} | {v['total']} | {v['correct']} | {percent(v['accuracy_all'])} | {v['valid_answers']} |\n"
    md+='\n| Item | Status | Prediction | Answer |\n|---|---|---|---|\n'
    for i in report['items']:md+=f"| {i['id']} | {i['status']} | {','.join(i['predicted_answer'] or [])} | {','.join(i['expected_answer'])} |\n"
    (out/'summary.md').write_text(md,encoding='utf-8')
    questions={i['id']:i for i in dataset['items']}
    rows=[]
    for i in report['items']:
        q=questions[i['id']];esc=lambda x:html.escape(str(x if x is not None else ''))
        detail=esc(q['stem'])+'\n\n'+'\n\n'.join(k+'. '+esc(v) for k,v in q['options'].items())
        content=esc(i['content']);reason=esc(i['reasoning_content'])
        detail+='\n\n提取方法：'+esc(i['extraction_method'])+'；选项：'+esc(','.join(i['predicted_answer'] or []))
        detail+='\n输出格式合规：'+esc(i['output_format_compliant'])
        rows.append(f"<tr data-status='{esc(i['status'])}' data-paper='{esc(i['paper_id'])}'><td>{esc(i['id'])}</td><td>{esc(i['status'])}</td><td>{esc(','.join(i['predicted_answer'] or []))}</td><td>{esc(','.join(i['expected_answer']))}</td><td><details><summary>题面 / 原始回答</summary><pre>{detail}</pre><h4>回答</h4><pre>{content}</pre><details><summary>reasoning_content</summary><pre>{reason}</pre></details></details></td></tr>")
    options=''.join(f'<option>{html.escape(v)}</option>' for v in sorted({i['status'] for i in report['items']}))
    papers=''.join(f'<option>{html.escape(v)}</option>' for v in report['by_paper'])
    page=f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>Benchmark evaluation</title>
<style>body{{font:16px system-ui;max-width:1300px;margin:32px auto;padding:16px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:10px;vertical-align:top}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}select{{margin:12px}}th{{background:#edf2f7}}</style>
<h1>{html.escape(report['profile'])}</h1><p>全量准确率 {percent(s['accuracy_all'])} · 有效答案准确率 {percent(s['accuracy_valid'])} · 有效答案率 {percent(s['completion_rate'])}</p><p>{html.escape(metric_note)}</p><p>{html.escape(report['note'])}</p>
<label>状态<select id="status"><option value="">全部</option>{options}</select></label><label>论文<select id="paper"><option value="">全部</option>{papers}</select></label><table><thead><tr><th>题号</th><th>状态</th><th>预测</th><th>答案</th><th>详情</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<script>const statusFilter=document.getElementById('status'),paperFilter=document.getElementById('paper');function filter(){{document.querySelectorAll('tbody tr').forEach(r=>r.hidden=(statusFilter.value&&r.dataset.status!==statusFilter.value)||(paperFilter.value&&r.dataset.paper!==paperFilter.value));}}statusFilter.onchange=paperFilter.onchange=filter;</script></html>'''
    (out/'report.html').write_text(page,encoding='utf-8')

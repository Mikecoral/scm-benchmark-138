"""Compare reports from the same questions and grading protocol."""
import argparse
import csv
import html
from pathlib import Path
from paper_search.benchmark.schema import read_json,write_json


def compare(runs,out):
    rows=[];reference=None
    for run in runs:
        run=Path(run);manifest=read_json(run/'manifest.json');report=read_json(run/'report.json');identity=manifest['identity']
        comparable={k:identity[k] for k in ['dataset_hash','answer_key_hash','selected_ids','system_prompt','output_protocol']}
        comparable['grading_protocol']=report.get('grading_protocol','legacy-grading')
        if reference is not None and comparable!=reference:raise ValueError('cannot compare different datasets, selected items or output protocols')
        reference=comparable;p=identity['profile']
        rows.append(dict(run=str(run),profile=identity['profile_name'],provider=p['provider'],model=p['model'],
                         mode=manifest['mode'],extra_body=p['extra_body'],max_tokens=p['max_tokens'],temperature=p.get('temperature'),
                         json_mode=p.get('json_mode',True),**report['summary']))
    if not rows:raise ValueError('no runs')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);write_json(out/'comparison.json',{'runs':rows,'comparable_identity':reference})
    fields=['profile','provider','model','mode','max_tokens','temperature','json_mode','total','correct','accuracy_all','accuracy_valid','completion_rate']
    with (out/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:
            safe={k:('\''+row[k] if isinstance(row[k],str) and row[k].startswith(('=','+','-','@')) else row[k]) for k in fields};w.writerow(safe)
    md='# Model comparison\n\nSame dataset, items and grading protocol. Check token budgets and thinking modes before interpreting differences.\n\n| Profile | Model | Total | Correct | Accuracy all | Accuracy valid | Max tokens | Mode |\n|---|---|---:|---:|---:|---:|---:|---|\n'
    for r in rows:
        fmt=lambda v:'N/A' if v is None else f'{v:.1%}'
        md+=f"| {r['profile']} | {r['model']} | {r['total']} | {r['correct']} | {fmt(r['accuracy_all'])} | {fmt(r['accuracy_valid'])} | {r['max_tokens']} | {r['mode']} |\n"
    (out/'comparison.md').write_text(md)
    page='<!doctype html><meta charset="utf-8"><title>Model comparison</title><style>body{font:16px system-ui;margin:40px}td,th{border:1px solid #ccc;padding:12px}table{border-collapse:collapse}</style><h1>Model comparison</h1><p>Same dataset and selection. Thinking modes and budgets may differ.</p><table><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in fields)+'</tr>'
    for row in rows:page+='<tr>'+''.join('<td>'+html.escape(str(row[k]))+'</td>' for k in fields)+'</tr>'
    (out/'comparison.html').write_text(page+'</table>')
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    try:compare(a.runs,a.out)
    except (ValueError,OSError,KeyError) as exc:p.exit(2,str(exc)+'\n')


if __name__=='__main__':main()

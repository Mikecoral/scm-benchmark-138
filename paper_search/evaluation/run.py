"""Evaluate each question in a fresh OpenAI-compatible request; resume safely."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import os
import re
from pathlib import Path
import time

from paper_search.benchmark.schema import read_json, strict_json, validate, write_json, digest, fingerprint
from .providers import validate_profile, request_body, call_once, output_protocol, system_prompt
from .report import build_report, write_report, score, grade


def resume_identity(identity):
    normalized=dict(identity,profile=dict(identity['profile']))
    normalized['profile'].pop('concurrency',None)
    return normalized


def read_records(path):
    records = {}
    path = Path(path)
    if not path.exists():return records
    # Recover an interrupted final append only; never silently skip an earlier corrupt row.
    data=path.read_bytes();lines=data.splitlines(keepends=True);valid_end=0
    for n,line in enumerate(lines):
        try:
            record=strict_json(line.decode('utf-8'))
            if not isinstance(record,dict) or not isinstance(record.get('id'),str):raise ValueError('invalid record')
        except (ValueError,UnicodeError):
            if n==len(lines)-1 and not line.endswith(b'\n'):
                backup=path.with_name('responses.interrupted-tail.bin')
                if backup.exists():raise ValueError('interrupted-tail recovery already exists; inspect manually')
                backup.write_bytes(line);path.write_bytes(data[:valid_end]);break
            raise ValueError('corrupt response journal at line '+str(n+1))
        records[record['id']]=record;valid_end+=len(line)
    if data and valid_end==len(data) and not data.endswith(b'\n'):
        with path.open('ab') as f:f.write(b'\n')
    return records


def run(args):
    dataset=read_json(args.dataset);key=read_json(args.answers);validate(dataset,key)
    if args.paper:dataset['items']=[i for i in dataset['items'] if i['paper_id'] in args.paper]
    if args.limit is not None:
        if args.limit<1:raise ValueError('limit must be positive')
        dataset['items']=dataset['items'][:args.limit]
    if not dataset['items']:raise ValueError('no selected items')
    ids={i['id'] for i in dataset['items']};key['items']=[i for i in key['items'] if i['id'] in ids];validate(dataset,key)
    config=read_json(args.config)
    if set(config)!={'profiles'} or args.profile not in config['profiles']:raise ValueError('unknown profile/config')
    profile=validate_profile(config['profiles'][args.profile])
    protocol=output_protocol(profile)
    credential=profile['api_key_env']
    direct_key=re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',credential) is None
    manifest_profile=dict(profile)
    if direct_key:
        manifest_profile['api_key_env']='inline-key-sha256:'+fingerprint(credential)
    if args.offline and (args.resume or args.retry_errors):raise ValueError('offline regrade uses a new output directory')
    identity=dict(dataset_hash=digest(args.dataset), answer_key_hash=digest(args.answers),
                  selected_ids=[i['id'] for i in dataset['items']], profile_name=args.profile,
                  profile=manifest_profile, system_prompt=system_prompt(profile), output_protocol=protocol,
                  rendered_requests_hash=fingerprint([request_body(i,profile) for i in dataset['items']]))
    if args.dry_run:
        for item in dataset['items']:request_body(item,profile)
        print('DRY RUN:',len(ids),'independent requests;',args.profile,'model='+profile['model'],
              'concurrency='+str(profile.get('concurrency',4)),'; no network calls')
        return
    api_key=None if args.offline else (credential if direct_key else os.environ.get(credential))
    if not args.offline and not api_key:raise ValueError(profile['api_key_env']+' is not set')
    out=Path(args.out)
    if args.resume:
        manifest=read_json(out/'manifest.json')
        if manifest.get('mode')!='api':raise ValueError('cannot resume an offline regrade as live API evaluation')
        if manifest.get('identity_hash')!=fingerprint(manifest['identity']):raise ValueError('invalid manifest identity hash')
        if resume_identity(manifest['identity'])!=resume_identity(identity):raise ValueError('resume refused: dataset/key/selection/model/config/protocol changed')
        if manifest['identity']['profile'].get('concurrency',4)!=profile.get('concurrency',4):
            manifest.setdefault('execution_changes',[]).append(dict(created_utc=datetime.now(timezone.utc).isoformat(),
                                                                 concurrency=profile.get('concurrency',4)))
            write_json(out/'manifest.json',manifest)
    else:
        out.mkdir(parents=True,exist_ok=False)
        write_json(out/'manifest.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),
                                          mode='offline' if args.offline else 'api',identity=identity,
                                          identity_hash=fingerprint(identity)))
    gold={i['id']:i['answer'] for i in key['items']}
    records=read_records(args.offline if args.offline else out/'responses.jsonl')
    if set(records)-ids:raise ValueError('response IDs outside selected dataset')
    if args.offline:
        # Keep a copy for reproducibility, including every supplied attempt.
        (out/'responses.jsonl').write_bytes(Path(args.offline).read_bytes())
    def refresh():
        result=build_report(dataset,key,records,args.profile,protocol);write_report(out,result,dataset);return result
    refresh()
    if not args.offline:
        from json import dumps
        pending=iter(i for i in dataset['items'] if not records.get(i['id']) or
                     (args.retry_errors and score(records[i['id']],gold[i['id']])['status']=='api_error'))
        concurrency=profile.get('concurrency',4)
        def attempt(item,delay):
            if delay:time.sleep(delay)
            record=call_once(item,profile,api_key)
            record['created_utc']=datetime.now(timezone.utc).isoformat()
            return record
        with (out/'responses.jsonl').open('a',encoding='utf-8') as journal, ThreadPoolExecutor(max_workers=concurrency) as pool:
            active={}
            def submit_next():
                item=next(pending,None)
                if item is not None:
                    base_attempt=records.get(item['id'],{}).get('attempt',0)
                    active[pool.submit(attempt,item,0)]=(item,base_attempt,0)
            for _ in range(concurrency):submit_next()
            while active:
                completed,_=wait(active,return_when=FIRST_COMPLETED)
                for future in completed:
                    item,base_attempt,retry=active.pop(future)
                    record=future.result();record['attempt']=base_attempt+retry+1
                    record['output_protocol']=protocol
                    journal.write(dumps(record,ensure_ascii=False,allow_nan=False)+'\n');journal.flush();os.fsync(journal.fileno())
                    records[item['id']]=record;current=refresh()
                    summary=current['summary'];received=len(records)
                    status=grade(record,gold[item['id']],protocol)['status']
                    print(f"{item['id']} {status} attempt={record['attempt']} | "
                          f"received={received}/{summary['total']} | "
                          f"accuracy_received={summary['correct']}/{received} ({summary['correct']/received:.2%}) | "
                          f"accuracy_all={summary['correct']}/{summary['total']} ({summary['accuracy_all']:.2%})",
                          flush=True)
                    transient=record.get('error') in {'URLError','TimeoutError','OSError'} or record.get('http_status') in {408,429,500,502,503,504}
                    if record.get('error') and transient and retry<profile['retries']:
                        delay=min(30,profile.get('retry_delay_seconds',1)*(2**retry))
                        active[pool.submit(attempt,item,delay)]=(item,base_attempt,retry+1)
                    else:submit_next()
    result=refresh();print('Accuracy all:',result['summary']['accuracy_all'],'valid:',result['summary']['accuracy_valid'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--answers',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--profile',required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--paper',action='append')
    p.add_argument('--limit',type=int);p.add_argument('--dry-run',action='store_true')
    p.add_argument('--resume',action='store_true');p.add_argument('--retry-errors',action='store_true')
    p.add_argument('--offline',type=Path,help='Regrade a response JSONL without API calls')
    args=p.parse_args()
    if args.retry_errors and not args.resume:p.error('--retry-errors requires --resume')
    if args.dry_run and (args.offline or args.resume):p.error('dry-run cannot combine with offline/resume')
    try:run(args)
    except (ValueError,OSError,KeyError) as exc:p.exit(2,str(exc)+'\n')


if __name__=='__main__':main()

"""Summarize own result directories/ZIPs without exporting credentials or raw logs."""
import argparse
from collections import Counter
import io
import json
from pathlib import Path
import re
import zipfile


def summarize(workflow, score, log):
    metrics = []
    features = None
    for line in log.splitlines():
        for marker in ('planner-metrics: ', 'planner-features: '):
            if marker in line:
                try:
                    value = json.loads(line.split(marker,1)[1])
                except ValueError:
                    continue
                if marker == 'planner-metrics: ':
                    metrics.append(value)
                else:
                    features = value
    repairs = re.findall(r'report feedback repairs=(\d+) false_since_repair=(\d+)',log)
    stages = {stage:Counter(re.findall(r'llm-stage '+stage+r': (\w+)',log))
              for stage in ('notice_parsing','night_planning')}
    return {'card':score.get('scenario'),'score':score.get('total'),
        'scenario_sha256':score.get('sha256',{}).get('scenario'),
        'components':score.get('components',{}),'counts':score.get('counts',{}),
        'termination':workflow.get('termination_reason',score.get('termination',{}).get('reason')),
        'clock':workflow.get('fair_clock',{}), 'features':features,
        'repairs':max((int(a) for a,b in repairs),default=0),
        'false_since_repair':int(repairs[-1][1]) if repairs else 0,
        'pace_levels':dict(Counter(re.findall(r'planner: pace level (\d+)',log))),
        'model_stages':stages,'model_timeouts':log.lower().count('timeout'),
        'invalid_actions':log.count('invalid action'),'planner_errors':log.count('planner error'),
        'metrics_samples':len(metrics),'last_metrics':metrics[-1] if metrics else None,
        'model_identity':next((line.split('llm model=',1)[1] for line in log.splitlines()
                              if 'llm model=' in line),None)}


def summarize_directory(folder):
    folder = Path(folder)
    workflow = json.loads((folder/'workflow_result.json').read_text(encoding='utf8'))
    score = workflow.get('score_report') or json.loads((folder/'score_report.json').read_text(encoding='utf8'))
    return summarize(workflow,score,(folder/'agent.log').read_text(encoding='utf8',errors='replace'))


def summarize_zip(content):
    rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        for name in sorted(names):
            if name.endswith('.zip'):
                rows.extend(summarize_zip(archive.read(name)))
            elif name.endswith('workflow_result.json'):
                prefix = name[:-len('workflow_result.json')]
                workflow = json.loads(archive.read(name))
                score = workflow.get('score_report') or json.loads(archive.read(prefix+'score_report.json'))
                log = archive.read(prefix+'agent.log').decode('utf8','replace') if prefix+'agent.log' in names else ''
                rows.append(summarize(workflow,score,log))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    rows=summarize_zip(args.source.read_bytes()) if args.source.is_file() else [summarize_directory(args.source)]
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps({'results':rows},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps([{'card':r['card'],'score':r['score'],'termination':r['termination']} for r in rows]))


if __name__=='__main__':
    main()

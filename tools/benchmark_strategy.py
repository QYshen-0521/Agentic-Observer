#!/usr/bin/env python3
"""Run immutable public-card strategy snapshots; never starts platform evaluations."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-dir', type=Path, default=ROOT/'agent')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cards', nargs='+', default=['L1','L2','L3','L4'])
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--with-model', action='store_true')
    parser.add_argument('--disable', nargs='*', choices=['integration','stagnation','requests','pacing'], default=[])
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.is_relative_to(args.agent_dir.resolve()):
        raise SystemExit('The output directory must be outside the source agent directory.')
    snapshot = out/'agent-snapshot'
    if snapshot.exists():
        raise SystemExit('Use a new output directory; snapshots must stay immutable.')
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(args.agent_dir,snapshot,ignore=shutil.ignore_patterns('.env','.env.*','__pycache__','*.pyc'))
    toggles = {'integration':('planner.py','self.integrated_quality'),
               'stagnation':('state.py','self.suppress_stagnation'),
               'requests':('planner.py','self.scheduled_requests'),
               'pacing':('planner.py','self.adaptive_pacing')}
    for feature in args.disable:
        name, marker = toggles[feature]
        path = snapshot/'agent_core'/name
        source = path.read_text(encoding='utf8')
        if f'{marker} = True' not in source:
            raise SystemExit(f'Cannot disable missing feature: {feature}')
        path.write_text(source.replace(f'{marker} = True',f'{marker} = False'),encoding='utf8')
    summary = []
    for card in args.cards:
        result = out/card
        result.mkdir()
        command = [sys.executable,str(ROOT/'local_runner.py'),'--card',card,'--agent-dir',str(snapshot),
                   '--wallclock','900','--out',str(result),'--with-model' if args.with_model else '--without-model']
        if args.env_file:
            command += ['--env-file',str(args.env_file.resolve())]
        env = os.environ.copy()
        if args.trace:
            env['AGENT_TRACE_PATH'] = str(result/'trace.jsonl')
        started = time.monotonic()
        with (result/'runner.log').open('w',encoding='utf8') as log:
            run = subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        if run.returncode:
            raise SystemExit(f'{card} failed; inspect {result / "runner.log"}')
        workflow = json.loads((result/'workflow_result.json').read_text(encoding='utf8'))
        score = workflow['score_report']
        log_text = (result/'agent.log').read_text(encoding='utf8',errors='replace')
        row = {'card':card,'score':score['total'],'components':score['components'],'counts':score['counts'],
               'termination':workflow['termination_reason'],'clock':workflow['fair_clock'],
               'elapsed_seconds':time.monotonic()-started,
               'pace_changes':log_text.count('planner: pace level'),
               'invalid_actions':log_text.count('invalid action'),
               'planner_errors':log_text.count('planner error'),
               'model_stages':{stage:log_text.count(f'llm-stage {stage}: ok')
                               for stage in ['notice_parsing','night_planning']}}
        summary.append(row)
        (out/'summary.json').write_text(json.dumps({'disabled':args.disable,'model':args.with_model,
            'results':summary,'mean_score':sum(r['score'] for r in summary)/len(summary)},indent=2),encoding='utf8')
        print(json.dumps({k:row[k] for k in ['card','score','termination','elapsed_seconds']}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

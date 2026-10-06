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
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'agent'))
from agent_core.features import FEATURES
from tools.result_diagnostics import summarize_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-dir', type=Path, default=ROOT/'agent')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cards', nargs='+', default=['L1','L2','L3','L4'])
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--with-model', action='store_true')
    parser.add_argument('--disable', nargs='*', choices=sorted(FEATURES), default=[])
    parser.add_argument('--fixed-level',type=int,choices=[0,1,2])
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
    files = {str(p.relative_to(snapshot)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(snapshot.rglob('*.py'))}
    engine = ROOT/'strategy_review_output/v4_rebuild/local-kit/examples/_local/runner/ENGINE_MANIFEST.json'
    metadata = {'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_hashes':files,'disabled':args.disable,'fixed_level':args.fixed_level,
        'engine_manifest_sha256':hashlib.sha256(engine.read_bytes()).hexdigest(),
        'model_enabled':args.with_model}
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf8')
    summary = []
    for card in args.cards:
        result = out/card
        result.mkdir()
        command = [sys.executable,str(ROOT/'local_runner.py'),'--card',card,'--agent-dir',str(snapshot),
                   '--wallclock','900','--out',str(result),'--with-model' if args.with_model else '--without-model']
        if args.env_file:
            command += ['--env-file',str(args.env_file.resolve())]
        env = os.environ.copy()
        env['OBSERVER_DISABLE_FEATURES'] = ','.join(args.disable)
        if args.fixed_level is not None:
            env['OBSERVER_FIXED_LEVEL'] = str(args.fixed_level)
        else:
            env.pop('OBSERVER_FIXED_LEVEL',None)
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
        row = {**summarize_directory(result),'card':card,'score':score['total'],'components':score['components'],'counts':score['counts'],
               'termination':workflow['termination_reason'],'clock':workflow['fair_clock'],
               'elapsed_seconds':time.monotonic()-started,
               'pace_changes':log_text.count('planner: pace level'),
               'invalid_actions':log_text.count('invalid action'),
               'planner_errors':log_text.count('planner error')}
        summary.append(row)
        expected = {name:name not in args.disable for name in FEATURES}
        if row.get('features') != expected:
            raise SystemExit(f'{card}: runtime feature receipt differs from requested ablation')
        (out/'summary.json').write_text(json.dumps({'disabled':args.disable,'model':args.with_model,
            'results':summary,'mean_score':sum(r['score'] for r in summary)/len(summary)},indent=2),encoding='utf8')
        print(json.dumps({k:row[k] for k in ['card','score','termination','elapsed_seconds']}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

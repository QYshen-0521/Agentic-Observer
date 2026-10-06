"""Bounded synthetic public-input stress check; not a card score or hidden-card simulation."""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from agent_core.geometry import parse_utc,format_utc
from agent_core.planner import Planner
from agent_core.state import SurveyState
from agent_core.validation import validate_action


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    os.environ['OBSERVER_MODEL_DISABLED']='1'
    data=json.loads((ROOT/'tests/fixtures/initialize.json').read_text(encoding='utf8'))
    now=parse_utc(data['survey']['start_utc'])
    rng=random.Random(2606)
    data['targets']['rows']=[[f'S{i}',rng.uniform(0,360),rng.uniform(-70,30),'BGS',rng.uniform(.3,3),rng.uniform(.3,2),i%20==0] for i in range(50000)]
    data['survey']['end_utc']=format_utc(now+timedelta(days=365))
    data['survey']['nights']=[{'observing_start_utc':format_utc(now+timedelta(days=n)),
                            'observing_end_utc':format_utc(now+timedelta(days=n,hours=9))} for n in range(365)]
    data['instrument'].update(grid_side=10,n_fibers=100,fov_side_deg=2.45,pitch_deg=.245,glass_side_deg=.245)
    started=time.process_time();state=SurveyState(data);planner=Planner(state)
    initialization=time.process_time()-started
    rows=[]
    for level in (0,1,2):
        state.fast_level=level
        planner._pro.rate_ema=0.0
        started=time.process_time()
        action=planner.plan(now,now+timedelta(hours=9),0,0)
        elapsed=time.process_time()-started
        validate_action(action,state,0,format_utc(now))
        rows.append({'level':level,'cpu_seconds':elapsed,'assignments':len(action['assignments']),**planner._pro.metrics})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    report={'synthetic':True,'targets':50000,'nights':365,'initialization_cpu_seconds':initialization,'results':rows}
    args.out.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report))


if __name__=='__main__':
    main()

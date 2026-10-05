"""V4 migration regressions: protocol safety, model flow and realized state."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from agent_core.advice import make_night_plan
from agent_core.geometry import altaz_to_radec, local_sidereal_deg, parse_utc, tangent_offsets
from agent_core.planner import Planner
from agent_core.state import SurveyState, PendingPrediction
from agent_core.validation import validate_action, ActionRejected
from agent_core.llm_client import LLMClient

def initial(side=4):
    data = json.loads((ROOT/'tests/fixtures/initialize.json').read_text(encoding='utf-8'))
    grid = data['instrument']
    grid.update(grid_side=side, n_fibers=side*side, fov_side_deg=side*grid['pitch_deg'])
    now = parse_utc(data['survey']['start_utc'])
    lst = local_sidereal_deg(now, data['site']['longitude_deg'])
    rows = []
    for i in range(40):
        ra, dec = altaz_to_radec(60+(i%8)*0.15, 170+(i//8)*0.2, lst, data['site']['latitude_deg'])
        rows.append([f'T{i}',ra,dec,'BGS',1.48,0.45,i<8])
    data['targets']['rows'] = rows
    return data

def request():
    return {'now_utc':'2026-10-02T00:00:00Z','observe_action_index':0,
            'latest_bulletin':{'notices':[]},'new_messages':[], 'last_result':None,
            'active_requests':[], 'wallclock':{'remaining_real_cpu_seconds':900,'wall_remaining_seconds':1800}}

class ModelFlowTests(unittest.TestCase):
    def test_connected_stages_affect_planner(self):
        state = SurveyState(initial())
        planner = Planner(state)
        calls=[]
        class Fake:
            def ask_json(self, system, data, left):
                calls.append(data)
                return {'avoid_directions':['NE'], 'duration_scale':1.2} if len(calls)==1 else {'required_priority':1.4,'duration_scale':1.1}
        planner.llm = Fake()
        payload=request()
        payload['latest_bulletin']['notices']=[{'event_kind':'cloud','direction':'NE'}]
        planner.night_index_seen=0
        planner._night_advice(parse_utc(payload['now_utc']),payload)
        self.assertEqual([c['stage'] for c in calls],['notice_parsing','night_planning'])
        self.assertEqual(calls[1]['parsed_weather']['avoid_directions'],['NE'])
        self.assertEqual(state.extra_avoid,{'NE'})
        self.assertAlmostEqual(state.duration_scale,1.32)
        self.assertEqual(planner.required_priority,1.4)

    def test_bad_advice_cannot_invent_weather_or_poison_numeric_state(self):
        state=SurveyState(initial())
        class Bad:
            def ask_json(self,*args):
                return {'avoid_directions':['NE','invented'], 'duration_scale':float('nan'), 'required_priority':float('inf')}
        plan=make_night_plan(Bad(),state,request(),[],'2026-10-01',0,1800,lambda _:None)
        self.assertEqual(plan['avoid_directions'],[])
        self.assertEqual(plan['duration_scale'],1.0)
        self.assertEqual(plan['required_priority'],1.0)

    def test_model_wait_cap_stops_further_network_requests(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake','OBSERVER_MODEL_DISABLED':'0'}):
            client=LLMClient()
            client.wait_used=client.max_wait_seconds
            with patch.object(client,'_attempt') as attempt:
                self.assertIsNone(client.ask_json('test',{},1800))
                attempt.assert_not_called()

class StateTests(unittest.TestCase):
    def test_only_real_feedback_updates_completion(self):
        state=SurveyState(initial())
        state.pending={'T0':PendingPrediction(1,1,60,170,False)}
        state.pending_duration=900
        state.pending_program='BACKUP'
        state.pending_action_index=0
        self.assertEqual(state.factor[0],0)
        state.on_result({'action':'observe','hits':[{'target_id':'T0','score':0.0}]},0)
        self.assertEqual(state.factor[0],0)
        state.pending={'T0':PendingPrediction(1,1,60,170,False)}
        state.pending_action_index=1
        state.on_result({'action':'observe','hits':[{'target_id':'T0','score':0.1}]},1)
        self.assertGreater(state.factor[0],0)
        self.assertLess(state.factor[0],state.scoring.required_threshold)

    def test_data_loss_rolls_back_invalidated_completion(self):
        state=SurveyState(initial())
        state.factor[:2]=[0.9,0.8]
        state.ledger=[(0,'T0',0.6),(1,'T0',0.9),(1,'T1',0.8)]
        state.on_messages([{'record_type':'state_resync','invalidated_window':{
            'action_index_start':1,'action_index_end_exclusive':2},
            'observed_target_ids':['T0'],'best_scores':[{'target_id':'T0','best_score':0.27}]}],None)
        self.assertEqual(state.factor[:2],[0.6,0.0])
        self.assertEqual(state.ledger,[(0,'T0',0.6)])

class ProtocolTests(unittest.TestCase):
    def test_rejects_invalid_actions_before_sending(self):
        state=SurveyState(initial())
        observe={'action':'observe','pointing':{'alt_deg':60,'az_deg':170},
                 'assignments':{'0':'T0'},'duration_seconds':900,'program':'DARK'}
        invalid=[{**observe,'duration_seconds':900.5},{**observe,'duration_seconds':True},
                 {**observe,'tile_id':'old'}, {**observe,'pointing':{'alt_deg':float('nan'),'az_deg':0}},
                 {**observe,'assignments':{'1':'T0','01':'T1'}},
                 {'action':'wait','duration_seconds':900,'until_utc':'2026-10-03T00:00:00Z'},
                 {'action':'wait','until_utc':'2026-10-01T00:00:00Z'}]
        for action in invalid:
            with self.subTest(action=action),self.assertRaises(ActionRejected):
                validate_action(action,state,0,'2026-10-02T00:00:00Z')
        self.assertEqual(validate_action({**observe,'assignments':{}},state)['assignments'],{})
        with self.assertRaises(ActionRejected):validate_action({'action':'report'},state,state.max_consecutive_reports)

    def test_non_four_by_four_grid_produces_geometrically_valid_observation(self):
        with patch.dict(os.environ,{'OBSERVER_MODEL_DISABLED':'1'}):
            state=SurveyState(initial(side=3))
            planner=Planner(state)
            planner.min_level=2
            action=planner.decide(request())
        action=validate_action(action,state)
        self.assertEqual(action['action'],'observe')
        self.assertTrue(action['assignments'])
        self.assertTrue(all(0<=int(f)<9 for f in action['assignments']))
        from agent_core.geometry import radec_to_altaz
        now=parse_utc(request()['now_utc'])
        lst=local_sidereal_deg(now,state.lon)
        for fiber,target in action['assignments'].items():
            i=state.index_of[target]
            alt,az=radec_to_altaz(state.ra[i],state.dec[i],lst,state.lat)
            offsets=tangent_offsets(alt,az,action['pointing']['alt_deg'],action['pointing']['az_deg'])
            actual,_=state.fiber_grid.classify(*offsets)
            self.assertEqual(actual,int(fiber))

    def test_repository_manifest_runs_independently_and_exits_on_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            shutil.copytree(ROOT/'agent',target/'agent',
                            ignore=shutil.ignore_patterns('.env','.env.*','__pycache__','*.pyc'))
            shutil.copy2(ROOT/'observer.project.json',target/'observer.project.json')
            manifest=json.loads((target/'observer.project.json').read_text())
            self.assertEqual(manifest['protocol'],'jsonl-v4')
            self.assertEqual(manifest['run'],['python3','-u','agent/agent.py'])
            stream=[{'protocol_version':'participant-agent-protocol-v4','message_type':'initialize','payload':initial()},
                    {'protocol_version':'participant-agent-protocol-v4','message_type':'decision_request',
                     'decision_sequence':1,'payload':request()},
                    {'protocol_version':'participant-agent-protocol-v4','message_type':'finish','payload':{}}]
            env={k:v for k,v in os.environ.items() if not k.startswith(('OPENAI_','KIMI_','MOONSHOT_','MODEL_'))}
            env['OBSERVER_MODEL_DISABLED']='1'
            done=subprocess.run([sys.executable,*manifest['run'][1:]],cwd=target,env=env,
                                input='\n'.join(json.dumps(x) for x in stream)+'\n',
                                text=True,capture_output=True,timeout=20)
            self.assertEqual(done.returncode,0,done.stderr)
            lines=done.stdout.splitlines()
            self.assertEqual(len(lines),1)
            response=json.loads(lines[0])
            self.assertEqual(response['protocol_version'],'participant-agent-protocol-v4')
            self.assertEqual(response['decision_sequence'],1)
            self.assertEqual(response['action'],'observe',done.stderr)
            self.assertNotIn('planner error',done.stderr)

if __name__=='__main__':unittest.main()

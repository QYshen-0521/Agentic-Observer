import unittest,os,copy
from unittest.mock import patch
from datetime import timedelta
from test_v4_agent import initial,request
from agent_core.state import SurveyState
from agent_core.planner import Planner
from agent_core.geometry import parse_utc,format_utc,local_sidereal_deg,radec_to_altaz,tangent_offsets
from agent_core.validation import validate_action

class ProCoreTests(unittest.TestCase):
    def test_gain_cache_does_not_change_the_selected_action(self):
        now=parse_utc(request()['now_utc'])
        p=Planner(SurveyState(initial()))
        cached=p.plan(now,now+timedelta(hours=9),0,0)
        q=Planner(SurveyState(initial()))
        with patch('agent_core.pro_planner.lru_cache',lambda **kwargs:lambda fn:fn):
            plain=q.plan(now,now+timedelta(hours=9),0,0)
        self.assertEqual(cached,plain)

    def test_live_search_uses_public_required_threshold_and_penalty(self):
        data=initial();data['scoring']['required']={'observed_factor_threshold':.7,'penalty_per_missing':80}
        p=Planner(SurveyState(data))
        self.assertEqual(p._pro.required_threshold,.7)
        self.assertEqual(p._pro.required_bonus,128)
        p.state.factor[0]=.6
        p._pro_plan(parse_utc(request()['now_utc']),parse_utc(request()['now_utc'])+timedelta(hours=9),0,0)
        self.assertEqual(p._pro.factor[0],.6)
        self.assertGreater(p._pro.value(0),p._pro.weight[0])

    def test_every_live_search_level_uses_dynamic_3_4_10_grid(self):
        now=parse_utc(request()['now_utc'])
        for side in (3,4,5,10):
            for level in (0,1,2):
                with self.subTest(side=side,level=level):
                    s=SurveyState(initial(side));p=Planner(s);s.fast_level=level
                    a=p.plan(now,now+timedelta(hours=9),0,0)
                    validate_action(a,s,0,format_utc(now));self.assertTrue(a['assignments'])
                    lst=local_sidereal_deg(now,s.lon)
                    for fiber,target in a['assignments'].items():
                        i=s.index_of[target];alt,az=radec_to_altaz(s.ra[i],s.dec[i],lst,s.lat)
                        actual=s.fiber_grid.classify(*tangent_offsets(alt,az,a['pointing']['alt_deg'],a['pointing']['az_deg']))[0]
                        self.assertEqual(actual,int(fiber))

    def test_new_request_requires_valid_new_window_exposure(self):
        now=parse_utc(request()['now_utc'])
        for seconds,action in ((60,'wait'),(3600,'observe')):
            data=initial();data['targets']['rows'][0][4]=.2
            s=SurveyState(data);s.required[:]=[False]*len(s.ids);s.factor[:]=[1]*len(s.ids)
            s.best_score[:]=[w*1.2 for w in s.weight];p=Planner(s)
            payload=request();payload['active_requests']=[{'request_id':'R','minimum_completed':1,'remaining_count':1,
                'completion_reward':100,'completion_factor_threshold':.5,'target_ids':['T0'],
                'completed_target_ids':[],'issued_at_utc':format_utc(now),'deadline_utc':format_utc(now+timedelta(seconds=seconds))}]
            with patch.dict(os.environ,{'OBSERVER_MODEL_DISABLED':'1'}):
                result=p.decide(payload)
            self.assertEqual(result['action'],action)
            if action=='observe':self.assertLessEqual(result['duration_seconds'],seconds)

    def test_connected_model_outputs_reach_the_live_search(self):
        p=Planner(SurveyState(initial()))
        class Fake:
            def ask_json(self,system,payload,left):
                return {'avoid_directions':['NE'],'duration_scale':1.2} if payload.get('stage')=='notice_parsing' else {'required_priority':1.4,'duration_scale':1.1}
        p.llm=Fake();payload=request();payload['latest_bulletin']={'notices':[{'event_kind':'cloud','direction':'NE'}]}
        a=p.decide(payload)
        self.assertEqual(a['action'],'observe')
        self.assertEqual(p._pro.extra_avoid,{'NE'})
        self.assertAlmostEqual(p._pro.duration_scale,1.32)
        self.assertAlmostEqual(p._pro.required_priority,1.4)

    def test_pointing_recalibration_discards_cached_miss_penalties(self):
        p=Planner(SurveyState(initial()))
        p._pro.vcache=[1.0]*len(p.state.ids)
        payload=request();payload['latest_bulletin']={'notices':[{'event_kind':'rain','direction':'ALL'}]}
        with patch.object(p.calibration,'feedback',return_value=True):
            p.decide(payload)
        self.assertIsNone(p._pro.vcache)

    def test_live_search_honors_the_diagnostic_program(self):
        p=Planner(SurveyState(initial()))
        p.state.force_program='BRIGHT'
        now=parse_utc(request()['now_utc'])
        action=p.plan(now,now+timedelta(hours=9),0,0)
        self.assertEqual(action['program'],'BRIGHT')

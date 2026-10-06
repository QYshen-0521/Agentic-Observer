"""Regressions exercise the live Pro path, public feedback and budget boundaries."""
import copy
import math
import os
import random
import unittest
from datetime import timedelta
from unittest.mock import patch

from test_v4_agent import initial, request
from agent_core.planner import Planner
from agent_core.state import SurveyState, PendingPrediction, FaultEvidence
from agent_core.calibration import PointingCalibration
from agent_core.geometry import parse_utc, format_utc, shift_altaz, tangent_offsets
from agent_core.features import FEATURES


class RobustStrategyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ,{'OBSERVER_DISABLE_FEATURES':'','OBSERVER_MODEL_DISABLED':'1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = parse_utc(request()['now_utc'])

    def make_request(self, need=1, seconds=3600):
        return {'request_id':'R','target_ids':['T0','T1','T2'],'remaining_count':need,
            'completed_target_ids':[],'minimum_completed':need,'completion_reward':100,
            'completion_factor_threshold':.5,'issued_at_utc':format_utc(self.now),
            'deadline_utc':format_utc(self.now+timedelta(seconds=seconds))}

    def test_one_request_credit_is_capped_with_overlapping_requests(self):
        p=Planner(SurveyState(initial()))._pro
        a=self.make_request();b=copy.deepcopy(a);b['request_id']='second';b['completion_reward']=40
        p.on_requests([a,b])
        self.assertEqual(p.request_value({0:1,1:1,2:1},self.now,60),420)
        self.assertEqual(p.request_value({0:1},self.now-timedelta(seconds=1),60),0)
        self.assertEqual(p.request_value({0:1},self.now,3601),0)

    def test_impossible_group_does_not_inflate_other_members(self):
        p=Planner(SurveyState(initial()))._pro
        r=self.make_request(need=3,seconds=60)
        p.flux[2]=1e-8
        p.on_requests([r]);p.filter_requests(self.now)
        self.assertEqual(p.request_groups,[])
        self.assertEqual(p.request_bonus,{})

    def test_completed_request_no_longer_has_planning_value(self):
        p=Planner(SurveyState(initial()))._pro
        r=self.make_request();r['remaining_count']=0
        p.on_requests([r])
        self.assertEqual(p.request_value({0:1},self.now,60),0)

    def test_public_last_opportunity_removes_failure_damping(self):
        p=Planner(SurveyState(initial()))
        core=p._pro
        core.required_nights[0]=[0,2,4]
        self.assertEqual(core.remaining_opportunities(0,3),1)
        self.assertGreater(core.required_urgency(0,3),core.required_urgency(0,0))
        t=core.threshold_duration(0,1,.8,.7)
        self.assertGreaterEqual(core.flux[0]*t*.8/core.f0t0,.7)
        core.factor[0]=1
        self.assertEqual(core.required_urgency(0,3),1)

    def test_live_last_chance_survives_failed_attempts(self):
        data=initial();data['targets']['rows']=data['targets']['rows'][:1]
        p=Planner(SurveyState(data));p.state.attempts[0]=100
        p._pro.required_nights[0]=[0]
        action=p.plan(self.now,self.now+timedelta(hours=6),0,0)
        self.assertEqual(action['action'],'observe')
        self.assertIn('T0',action['assignments'].values())
        self.assertGreaterEqual(p._pro.pending['T0']['pred'],p.state.scoring.required_threshold)

    def test_public_windows_reused_and_calendar_is_lazy(self):
        p=Planner(SurveyState(initial()))
        self.assertIs(p._pro.first_night,p.state.first_night)
        self.assertEqual(p._pro.night_best,{})
        p._pro.best_future_model(0,0)
        self.assertEqual(set(p._pro.night_best),{0})

    def test_repair_clears_both_quality_estimators(self):
        p=Planner(SurveyState(initial()));p.report_pending=True
        p.state.clean_history=[(0,0,.1)]
        p._pro.samples.append((0,.1));p._pro.band_obs.append((0,1,'DARK',True,True))
        p._on_report_result({'action':'report','correct':True,'repaired':True})
        self.assertFalse(p.state.clean_history)
        self.assertFalse(p._pro.samples)
        self.assertFalse(p._pro.band_obs)
        self.assertEqual(p._pro.scale,1)

    def test_five_by_five_live_search_and_integration_receipt(self):
        p=Planner(SurveyState(initial(5)))
        action=p.decide(request())
        self.assertEqual(action['action'],'observe')
        self.assertTrue(p._pro.metrics['integration'])
        self.assertTrue(p._pro.metrics['joint_program'])
        self.assertTrue(all(0<=int(f)<25 for f in action['assignments']))
        self.assertTrue(p._pro.exposure_model.exposures)

    def test_joint_program_search_matches_best_forced_program(self):
        data=initial()
        for row in data['targets']['rows']:row[6]=False
        def average(model,i,duration):
            return .3 if i%3==0 else .9
        scores={}
        with patch('agent_core.exposure.ExposureModel.average',average):
            for program in (None,'DARK','BRIGHT','BACKUP'):
                p=Planner(SurveyState(data));p.state.force_program=program
                p.plan(self.now,self.now+timedelta(hours=6),0,0)
                scores[program]=p._pro.metrics['net_gain']
        self.assertAlmostEqual(scores[None],max(scores[p] for p in ('DARK','BRIGHT','BACKUP')))

    def test_wall_pressure_changes_progress_target_and_can_recover(self):
        p=Planner(SurveyState(initial()))
        p.clock.wall_left=130;p.clock.cpu_left=900;p.clock.platform_cost=2
        p._pace(self.now)
        self.assertGreater(p._pro.min_progress_seconds,0)
        p.clock.wall_left=100000;p.clock.platform_cost=.001
        p._pace(self.now)
        self.assertEqual(p._pro.min_progress_seconds,0)

    def test_ablation_switches_reach_live_search_and_fixed_pace(self):
        with patch.dict(os.environ,{'OBSERVER_DISABLE_FEATURES':','.join(FEATURES),'OBSERVER_FIXED_LEVEL':'2'}):
            p=Planner(SurveyState(initial()))
            payload=request();payload['active_requests']=[self.make_request()]
            p.decide(payload)
            self.assertEqual(p.state.fast_level,2)
            self.assertFalse(p._pro.joint_program)
            self.assertFalse(p._pro.protect_required)
            self.assertFalse(p._pro.shared_quality)
            self.assertFalse(p._pro.diverse_search)
            self.assertIsNone(p._pro.exposure_model)
            self.assertEqual(p._pro.request_groups,[])
            self.assertFalse(p.calibration.expand_search)

    def test_ambiguous_saturation_is_not_a_quality_sample(self):
        s=SurveyState(initial());s.pending_program='DARK';s.pending_duration=900
        s.pending={'T0':PendingPrediction(1,1,60,170,True)}
        s.on_result({'action':'observe','hits':[{'target_id':'T0','score':s.weight[0]*s.scoring.mismatch_multiplier}]},0)
        self.assertEqual(len(s._samples),0)
        self.assertLess(s.factor[0],s.factor_high[0])
        self.assertFalse(s._band_checks[-1][2])

    def test_quality_uses_one_sample_per_exposure_and_shared_scale(self):
        p=Planner(SurveyState(initial()));s=p.state
        s.pending_program='DARK';s.pending_duration=900
        s.pending={f'T{i}':PendingPrediction(1,1,60,170,True) for i in range(8)}
        s.on_result({'action':'observe','hits':[{'target_id':f'T{i}','score':s.weight[i]*.2} for i in range(8)]},0)
        self.assertEqual(len(s._samples),1)
        p.plan(self.now,self.now+timedelta(hours=6),0,0)
        self.assertEqual(p._pro.scale,s.scale)
        self.assertEqual(p._pro.calibrated_band_scale(0),s.band_scale)

    def test_confirmed_unsaturated_match_remains_quality_evidence(self):
        p=Planner(SurveyState(initial()))._pro
        p.pending_program='DARK';p.pending_duration=900
        p.pending={'T0':{'model':1,'band_model':1,'clean':True,'dir_clean':True,'az':170,'alt':60}}
        p.on_result({'action':'observe','hits':[{'target_id':'T0','score':p.weight[0]*.8}]},self.now,0)
        self.assertEqual(len(p.samples),1)
        self.assertEqual(p.scale,p.samples[-1][1])

    def test_model_veto_does_not_set_report_cooldown(self):
        p=Planner(SurveyState(initial()));p.false_reports=9
        p.suspicion_hours=[0,6]
        evidence=FaultEvidence(.1,1,.1,12,3,24,8,8)
        with patch.object(p.state,'fault_evidence',return_value=evidence),patch.object(p.llm,'ask_json',return_value={'report':False}):
            self.assertIsNone(p._maybe_report(12,request()))
        self.assertEqual(p.last_report_hours,float('-inf'))
        self.assertEqual(p.last_veto_hours,12)
        self.assertFalse(p.report_pending)

    def test_announced_earthquake_gates_and_weather_requires_clean_evidence(self):
        p=Planner(SurveyState(initial()))
        p.earthquake_until=20
        with patch.object(p.state,'fault_evidence',return_value=None) as evidence:
            self.assertIsNone(p._maybe_report(12,request()))
            evidence.assert_not_called()
        p.earthquake_until=0;p.state.notices={'overcast|ALL'}
        with patch.object(p.state,'fault_evidence',return_value=None) as evidence:
            self.assertIsNone(p._maybe_report(12,request()))
            evidence.assert_called_once()

    def test_diagnostic_program_is_bounded_in_time(self):
        p=Planner(SurveyState(initial()));p.false_reports=9
        e=FaultEvidence(.1,1,.1,12,3,24,8,0)
        with patch.object(p.state,'fault_evidence',return_value=e):
            p._maybe_report(12,request());self.assertEqual(p.state.force_program,'DARK')
            for n in range(1,6):
                p._maybe_report(12+n*.1,request());self.assertEqual(p.state.force_program,'DARK')
            p._maybe_report(12.6,request());self.assertIsNone(p.state.force_program)

    def test_calibration_expands_beyond_old_search_box(self):
        s=SurveyState(initial());c=PointingCalibration(s.fiber_grid);rng=random.Random(21)
        expected=(s.fiber_grid.pitch*1.7,-s.fiber_grid.pitch*1.4)
        for exposure in range(10):
            center=(45+exposure*3,90+exposure*11)
            hits=[];pending=[]
            for j in range(16):
                fiber=rng.randrange(s.fiber_grid.n)
                north,east=s.fiber_grid.fiber_center(fiber)
                alt,az=shift_altaz(center[0]+expected[0],center[1]+expected[1],
                    north+rng.uniform(-.65,.65)*s.fiber_grid.pitch,east+rng.uniform(-.65,.65)*s.fiber_grid.pitch)
                target=f'{exposure}-{j}'
                actual=s.fiber_grid.classify(*tangent_offsets(alt,az,center[0]+expected[0],center[1]+expected[1]))[0]
                if actual==fiber:hits.append({'target_id':target,'score':0})
                pending.append((*center,alt,az,fiber,target))
            c.pending=pending;c.feedback({'action':'observe','hits':hits})
        self.assertLess(max(abs(a-b) for a,b in zip(c.offset,expected)),s.fiber_grid.pitch*.2)


if __name__=='__main__':
    unittest.main()

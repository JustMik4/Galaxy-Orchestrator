import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
import multicontroller as mc
import installer


def evidence():
    return dict(mode='CO-OP',author='owner',reviewer='owner',head='h',tested_head='h',reviewed_head='h',
                base='b',tested_base='b',reviewed_base='b',implementer_session='worker-1',reviewer_session='review-2',
                review_result='passed',grant_revision=2,current_revision=2,dependencies_merged=True,
                checks={'tests':'success'},required_checks=['tests'],blockers=[],conversations_resolved=True,
                mergeable=True,protection_verified=True)


class OptionalCoopTests(unittest.TestCase):
    def test_same_operator_fresh_reviewer_can_integrate_without_partner(self):
        self.assertEqual(mc.gate(evidence()), [])

    def test_independent_session_and_success_remain_required(self):
        for field,value in [('reviewer_session','worker-1'),('reviewer_session',None),('review_result','failed')]:
            with self.subTest(field=field,value=value):
                e=evidence(); e.update(reviewer='partner',**{field:value})
                self.assertTrue(mc.gate(e))

    def test_primary_can_reclaim_without_old_owner_ack(self):
        self.assertTrue(hasattr(mc,'reclaim'), 'reclaim not implemented')
        old=dict(task='42',owner='partner',machine='pc2',nonce='old',revision=1,scope=['src/auth/'])
        new=dict(old,owner='owner',machine='pc1',nonce='new',revision=2)
        result=mc.reclaim([old],new,actor='owner',lead='owner',expected_revision=1,
                          integration_fenced=True,reason='Partner unavailable, primary resumes')
        self.assertEqual(result['active'][0]['owner'],'owner')
        self.assertEqual(result['retired']['nonce'],'old')
        self.assertEqual(old['owner'],'partner')

    def test_reclaim_rejects_unfenced_stale_and_unauthorized_requests(self):
        self.assertTrue(hasattr(mc,'reclaim'), 'reclaim not implemented')
        old=dict(task='42',owner='partner',machine='pc2',nonce='old',revision=1,scope=['src/auth/'])
        args=dict(active=[old],request=dict(old,owner='owner',nonce='new',revision=2),actor='owner',lead='owner',
                  expected_revision=1,integration_fenced=True,reason='resuming')
        for key,value in [('integration_fenced',False),('expected_revision',0),('actor','partner')]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError): mc.reclaim(**dict(args,**{key:value}))

    def test_coop_product_gate_accepts_one_operator(self):
        with tempfile.TemporaryDirectory(prefix='mc optional ') as tmp:
            p=Path(tmp)
            installer.install(ROOT,p,mode='CO-OP')
            team=json.loads((p/'AGENT_TEAM.yml').read_text())
            team.update(integration_operators=['owner'],operators=[dict(id='owner',github_login='owner')])
            (p/'AGENT_TEAM.yml').write_text(json.dumps(team))
            (p/'.multicontroller/checks.json').write_text(json.dumps({'commands':[[sys.executable,'-c','pass']]}))
            self.assertEqual(installer.validate_project(p,run_checks=True)['product_checks'],'passed')

    def test_upgrade_migrates_old_cross_review_requirement_preserving_identity(self):
        with tempfile.TemporaryDirectory(prefix='mc migration ') as tmp:
            p=Path(tmp)
            installer.install(ROOT,p,mode='CO-OP')
            team=json.loads((p/'AGENT_TEAM.yml').read_text())
            team.update(schema_version=1,integration_lead='owner',operators=[dict(id='owner',github_login='owner')])
            team['rules']['cross_review']=True
            team.pop('review',None)
            (p/'AGENT_TEAM.yml').write_text(json.dumps(team))
            installer.install(ROOT,p,mode='CO-OP')
            updated=json.loads((p/'AGENT_TEAM.yml').read_text())
            self.assertEqual(updated['rules']['cross_review'],'optional')
            self.assertEqual(updated['operators'],team['operators'])

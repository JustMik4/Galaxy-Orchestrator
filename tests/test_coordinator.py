import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
if importlib.util.find_spec('coordinator'):
    import coordinator as co
else: co=None


class Store:
    def __init__(self): self.state=dict(version=0,active=[],receipts={},pending=None); self.merges=[]
    def read(self): return copy.deepcopy(self.state)
    def save(self,state): self.state=copy.deepcopy(state)
    def pr(self,n): return dict(number=n,merged=bool(self.merges),merge_commit_sha='merged',state='open',draft=False,head={'sha':'h'},base={'sha':'b','ref':'main'},mergeable=True)
    def files(self,n): return [{'filename':'src/a.py'}]
    def merge(self,n,sha): self.merges.append((n,sha)); return {'merged':True,'sha':'merged'}
    def branch(self,name): return {'protected':True}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(co,'serialized coordinator missing')
        self.store=Store()
        self.team=dict(operators=[dict(id='one',github_login='alice'),dict(id='two',github_login='bob')],
                       integration_operators=['one','two'],integration_branch='main',required_checks=['tests'])
    def req(self,actor='one',version=0,id='r1',operation='claim'):
        return dict(id=id,expected_version=version,operation=operation,task=dict(task='42',owner=actor,machine='pc',nonce=id,revision=1,scope=['src/'],depends_on=[]))
    def test_either_operator_can_claim_and_duplicate_is_idempotent(self):
        q=self.req('two')
        a=co.run(self.store,self.team,'bob',q)
        self.assertEqual(a,co.run(self.store,self.team,'bob',q))
        self.assertEqual(self.store.state['active'][0]['owner'],'two')
    def test_second_competing_request_fails_stale_version(self):
        co.run(self.store,self.team,'alice',self.req())
        with self.assertRaises(ValueError): co.run(self.store,self.team,'bob',self.req('two',id='r2'))
        self.assertEqual(len(self.store.state['active']),1)
    def test_partner_can_reclaim_without_original_actor_online(self):
        co.run(self.store,self.team,'alice',self.req())
        q=self.req('two',1,'r2','reclaim'); q['task']['revision']=2; q['reason']='former owner unavailable'
        co.run(self.store,self.team,'bob',q)
        self.assertEqual(self.store.state['active'][0]['owner'],'two')
    def test_unauthorized_and_impersonated_claim_fail(self):
        for actor in ['outsider','bob']:
            with self.assertRaises(ValueError): co.run(self.store,self.team,actor,self.req('one'))
    def test_late_release_does_not_delete_new_grant(self):
        co.run(self.store,self.team,'alice',self.req())
        q=self.req('two',1,'r2','reclaim'); q['task']['revision']=2;q['reason']='resuming'
        co.run(self.store,self.team,'bob',q)
        old=self.req('one',2,'r3','release')
        with self.assertRaises(ValueError): co.run(self.store,self.team,'alice',old)
    def test_id_reuse_with_changed_payload_fails(self):
        co.run(self.store,self.team,'alice',self.req())
        changed=self.req();changed['task']['scope']=['other/']
        with self.assertRaises(ValueError):co.run(self.store,self.team,'alice',changed)

    def merge_request(self):
        from test_optional_coop import evidence
        co.run(self.store,self.team,'alice',self.req())
        return dict(id='merge1',expected_version=1,operation='merge',task=self.req()['task'],pr=7,evidence=evidence())

    def test_merge_is_serialized_and_consumes_grant(self):
        q=self.merge_request()
        co.run(self.store,self.team,'bob',q)
        self.assertEqual(self.store.merges,[(7,'h')])
        self.assertEqual(self.store.state['active'],[])
        self.assertIsNone(self.store.state['pending'])

    def test_merge_rejects_stale_grant_and_out_of_scope_files(self):
        q=self.merge_request();q['task']['revision']=2
        with self.assertRaises(ValueError):co.run(self.store,self.team,'bob',q)
        q['task']['revision']=1
        self.store.files=lambda n:[{'filename':'secrets.txt'}]
        with self.assertRaises(ValueError):co.run(self.store,self.team,'bob',q)
        self.assertEqual(self.store.merges,[])

    def test_merge_crash_can_be_reconciled_by_other_operator(self):
        q=self.merge_request()
        original=self.store.merge
        def crash(n,sha): original(n,sha);raise OSError('response lost')
        self.store.merge=crash
        with self.assertRaises(OSError):co.run(self.store,self.team,'alice',q)
        self.assertIsNotNone(self.store.state['pending'])
        with self.assertRaises(ValueError):co.run(self.store,self.team,'bob',self.req('two',1,'r2','reclaim'))
        co.run(self.store,self.team,'bob',dict(id='recovery',operation='recover',pending_id='merge1'))
        self.assertIsNone(self.store.state['pending'])
        self.assertEqual(len(self.store.merges),1)

    def test_closed_changed_pr_can_abort_pending_merge(self):
        q=self.merge_request()
        def fail(n,sha):raise OSError('network lost before merge')
        self.store.merge=fail
        with self.assertRaises(OSError):co.run(self.store,self.team,'alice',q)
        original=self.store.pr
        self.store.pr=lambda n:dict(original(n),head={'sha':'new'},state='closed',merged=False)
        result=co.run(self.store,self.team,'bob',dict(id='recover2',operation='recover',pending_id='merge1'))
        self.assertTrue(result['aborted'])
        self.assertIsNone(self.store.state['pending'])

    def test_reclaim_cannot_drop_dependencies(self):
        q=self.req();q['task']['depends_on']=[9]
        co.run(self.store,self.team,'alice',q)
        q=self.req('two',1,'r2','reclaim');q['task']['revision']=2;q['reason']='resume'
        with self.assertRaises(ValueError):co.run(self.store,self.team,'bob',q)

    def test_team_loader_prefers_canonical_v2_and_preserves_v1_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = {
                'schema_version': 1, 'mode': 'CO-OP',
                'operators': [{'id': 'v2', 'github_login': 'alice'}],
            }
            legacy = {
                'schema_version': 3, 'mode': 'CO-OP',
                'operators': [{'id': 'v1', 'github_login': 'bob'}],
            }
            (root / '.galaxy').mkdir()
            (root / '.galaxy/team.yml').write_text(json.dumps(canonical))
            (root / 'AGENT_TEAM.yml').write_text(json.dumps(legacy))

            self.assertEqual(co.load_team(root), canonical)
            (root / '.galaxy/team.yml').unlink()
            self.assertEqual(co.load_team(root), legacy)

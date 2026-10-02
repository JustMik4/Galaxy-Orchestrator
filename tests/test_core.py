import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
if importlib.util.find_spec('multicontroller'):
    import multicontroller as mc
else:
    mc = None


def event(n=1, **kw):
    value = dict(task='42', attempt_id=str(n), agent_id='a', model='gpt-6-luna',
                 effort='medium', result='failed', failure_class='implementation', signature='atomicity')
    value.update(kw)
    return value


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(mc, 'decision engine not implemented')

    def test_first_repair_and_second_identical_failure_use_fresh_agent(self):
        self.assertEqual(mc.decide([event()])['action'], 'repair')
        self.assertEqual(mc.decide([event(), event(2)])['action'], 'fresh_agent')

    def test_fresh_failure_escalates_without_resetting_total_budget(self):
        h = [event(), event(2), event(3, agent_id='b')]
        self.assertEqual(mc.decide(h)['effort'], 'high')
        h.append(event(4, agent_id='c', effort='high'))
        self.assertEqual(mc.decide(h)['action'], 'circuit_breaker')

    def test_infra_and_scope_do_not_escalate(self):
        self.assertEqual(mc.decide([event(failure_class='infra')])['action'], 'blocked')
        self.assertEqual(mc.decide([event(failure_class='scope')])['action'], 'quarantine')

    def test_context_insufficient_retries_same_level_without_reputation_penalty(self):
        history = [event(failure_class='context-insufficient')]
        decision = mc.decide(history)
        self.assertEqual(decision['action'], 'fresh_agent')
        self.assertEqual((decision['model'], decision['effort']), ('gpt-6-luna', 'medium'))
        from datetime import datetime, timezone
        history[0]['timestamp'] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(mc.reputation(history), [])

    def test_reasoning_and_complexity_escalate_differently(self):
        self.assertEqual(mc.decide([event(failure_class='reasoning')])['model'], 'gpt-6-luna')
        self.assertEqual(mc.decide([event(failure_class='complexity')])['model'], 'gpt-6.1-sol')

    def test_previous_generation_history_remains_readable(self):
        previous = event(model='gpt-5.6-luna', effort='medium', failure_class='reasoning')
        decision = mc.decide([previous])
        self.assertEqual(decision['action'], 'escalate')
        self.assertEqual((decision['model'], decision['effort']), ('gpt-6-luna', 'high'))

    def test_unknown_classes_fail_closed_and_duplicate_events_rejected(self):
        with self.assertRaises(ValueError): mc.decide([event(failure_class='mystery')])
        with self.assertRaises(ValueError): mc.decide([event(), event()])

    def test_time_budget_stops_with_unknown_tokens(self):
        self.assertEqual(mc.decide([event()], elapsed_minutes=45)['action'], 'circuit_breaker')

    def test_success_does_not_need_another_attempt(self):
        self.assertEqual(mc.decide([event(result='passed', failure_class=None)])['action'], 'complete')

    def test_path_overlap_is_case_insensitive_and_boundary_aware(self):
        self.assertTrue(mc.overlap('src/Auth/', 'src/auth/token.py'))
        self.assertFalse(mc.overlap('src/auth/', 'src/authorization/a.py'))
        for p in ('../secret', 'C:/file', 'src\\a', '/root', 'src/*', 'src/../a', 'src//'):
            with self.assertRaises(ValueError): mc.overlap(p, 'src/a')

    def test_dag_rejects_cycles_and_closed_without_merge(self):
        with self.assertRaises(ValueError): mc.check_dag({'a': ['b'], 'b': ['a']})
        with self.assertRaises(ValueError): mc.check_dag({'a': ['missing']})
        self.assertEqual(mc.check_dag({'a': [], 'b': ['a']}), ['a', 'b'])

    def test_serial_claim_reserves_before_ack_and_rejects_second_pc(self):
        req = dict(task='42', owner='one', machine='pc1', nonce='n1', revision=1, scope=['src/auth/'])
        active = mc.grant([], req, 'lead', 'lead')
        rival = dict(req, task='43', owner='two', machine='pc2', nonce='n2', scope=['src/Auth/a.py'])
        with self.assertRaises(ValueError): mc.grant(active, rival, 'lead', 'lead')
        with self.assertRaises(ValueError): mc.grant([], req, 'impostor', 'lead')
        self.assertEqual(len(active), 1)

    def test_gate_binds_current_head_review_claim_and_checks(self):
        e = dict(head='abc', tested_head='abc', reviewed_head='abc', author='one', reviewer='two',
                 implementer_session='worker',reviewer_session='review',review_result='passed',
                 base='base1', tested_base='base1', reviewed_base='base1',
                 mode='CO-OP', grant_revision=2, current_revision=2, dependencies_merged=True,
                 checks={'product': 'success'}, required_checks=['product'], blockers=[],
                 conversations_resolved=True, mergeable=True, protection_verified=True)
        self.assertEqual(mc.gate(e), [])
        for field, val in [('head','def'), ('reviewer_session','worker'), ('current_revision',3), ('dependencies_merged',False)]:
            self.assertTrue(mc.gate(dict(e, **{field: val})), field)
        self.assertTrue(mc.gate(dict(e, checks={})))
        for field, value in [('mode', None), ('mode', 'coop'), ('reviewer',''), ('base','new-base')]:
            self.assertTrue(mc.gate(dict(e, **{field:value})), field)

    def test_claim_rejects_malformed_scope_snapshot(self):
        r = dict(task='42', owner='one', machine='pc', nonce='n', revision=1, scope='src')
        with self.assertRaises(ValueError): mc.grant([], r, 'lead','lead')
        with self.assertRaises(ValueError): mc.grant([r], dict(r, task='43', scope=['src']), 'lead','lead')

    def test_usage_deduplicates_and_does_not_count_cache_twice(self):
        e = dict(event_id='a', role='worker', model='luna', effort='medium', input_tokens=100,
                 output_tokens=20, cached_tokens=40, counter_mode='delta')
        r = mc.usage([e, e, dict(e, event_id='b', input_tokens=None, output_tokens=None, cached_tokens=None)])
        self.assertEqual(r['known_total_tokens'], 120)
        self.assertEqual(r['unknown_events'], 1)
        with self.assertRaises(ValueError): mc.usage([dict(e, counter_mode='cumulative')])

    def test_reputation_ignores_infra_deduplicates_tasks_and_only_proposes(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        events = [dict(event(i), task=str(i), timestamp=now, task_class='concurrency', project='demo') for i in range(1,4)]
        events += [dict(events[0], attempt_id='repair'), dict(events[0], task='infra', failure_class='infra')]
        result = mc.reputation(events)
        self.assertEqual(result[0]['tasks'], 3)
        self.assertEqual(result[0]['proposal'], 'review_promotion')
        self.assertEqual(result[0]['policy_mutated'], False)

    def test_high_level_success_is_not_evidence_of_cheap_success(self):
        from datetime import datetime, timezone
        h = [dict(event(i), task=str(i), timestamp=datetime.now(timezone.utc).isoformat(),
                  result='passed', effort='high') for i in range(5)]
        self.assertIsNone(mc.reputation(h)[0]['proposal'])


if __name__ == '__main__': unittest.main()

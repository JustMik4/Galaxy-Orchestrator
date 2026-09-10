"""Deterministic policy helpers. No model calls, network writes or account access."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

LADDER = [('gpt-5.6-luna', 'low'), ('gpt-5.6-luna', 'medium'),
          ('gpt-5.6-luna', 'high'), ('gpt-5.6-sol', 'high'), ('gpt-6-astra', 'high')]
CLASSES = {'implementation', 'reasoning', 'complexity', 'architecture', 'ambiguity',
           'scope', 'regression', 'infra', 'flaky', 'git'}


def decide(history, preset='balanced', elapsed_minutes=0, known_tokens=None, token_limit=None):
    if preset not in ('balanced', 'critical'): raise ValueError('unknown preset')
    cap, minutes = (4, 45) if preset == 'balanced' else (5, 60)
    ids = set()
    for e in history:
        for k in ('task', 'attempt_id', 'agent_id', 'model', 'effort', 'result'):
            if not e.get(k): raise ValueError('missing ' + k)
        if e['attempt_id'] in ids: raise ValueError('duplicate attempt')
        ids.add(e['attempt_id'])
        if e['task'] != history[0]['task']: raise ValueError('mixed tasks')
        if e['result'] not in ('passed', 'failed'): raise ValueError('invalid result')
        if (e['model'], e['effort']) not in LADDER and (e['model'], e['effort']) != ('gpt-5.6-sol', 'medium'):
            raise ValueError('unrecognized model/effort')
        if e['result'] == 'failed' and (e.get('failure_class') not in CLASSES or not e.get('signature')):
            raise ValueError('class and signature required')
    result = dict(action='start', model='gpt-5.6-luna', effort='medium', attempts_used=len(history),
                  attempts_remaining=max(0, cap-len(history)))
    def answer(action, reason):
        return dict(result, action=action, reason=reason)
    if any(e.get('failure_class') in ('scope', 'regression') for e in history):
        return answer('quarantine', 'Contract violation or severe regression; root review required')
    if history:
        last = history[-1]
        result.update(model=last['model'], effort=last['effort'])
        if last['result'] == 'passed': return answer('complete', 'Verified outcome recorded')
    if len(history) >= cap or elapsed_minutes >= minutes or (token_limit is not None and known_tokens is not None and known_tokens >= token_limit):
        return answer('circuit_breaker', 'Aggregate budget exhausted; no additional dispatch')
    if not history: return answer('start', 'Initial bounded attempt')
    category = last['failure_class']
    if category in ('infra', 'flaky', 'git'): return answer('blocked', 'Repair environment or coordination; do not promote model')
    if category in ('architecture', 'ambiguity'): return answer('root', 'Contract must be revised')
    # Budgets inferred from actual dispatch transitions, so a role rename cannot reset them.
    same_repairs = sum(a['agent_id'] == b['agent_id'] for a, b in zip(history, history[1:]))
    fresh_retries = sum(a['agent_id'] != b['agent_id'] and (a['model'], a['effort']) == (b['model'], b['effort'])
                        for a, b in zip(history, history[1:]))
    if category == 'implementation':
        if same_repairs == 0:
            return answer('repair', 'One bounded repair with a changed hypothesis')
        if fresh_retries == 0:
            return answer('fresh_agent', 'Stop previous agent; one clean context at same level')
    position = LADDER.index((last['model'], last['effort'])) if (last['model'], last['effort']) in LADDER else 3
    next_position = max(position+1, 3) if category == 'complexity' else position+1
    if next_position >= len(LADDER): return answer('root', 'Highest automatic route exhausted')
    result.update(model=LADDER[next_position][0], effort=LADDER[next_position][1])
    return answer('escalate', 'Fresh context at justified higher route; consumes total attempt budget')


def normalized_path(value):
    if not isinstance(value, str) or not value or value.startswith('/') or re.search(r'[\\:*?\[\]<>|\x00-\x1f]', value):
        raise ValueError('scope must be a literal relative path')
    parts = value.removesuffix('/').split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts): raise ValueError('unsafe scope')
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
        raise ValueError('Windows reserved path')
    return value.casefold()


def overlap(a, b):
    a, b = normalized_path(a), normalized_path(b)
    return a.rstrip('/') == b.rstrip('/') or (a.endswith('/') and b.startswith(a)) or (b.endswith('/') and a.startswith(b))


def check_dag(tasks):
    visiting, visited, order = set(), set(), []
    def visit(task):
        if task not in tasks: raise ValueError('missing dependency: ' + task)
        if task in visiting: raise ValueError('dependency cycle')
        if task in visited: return
        visiting.add(task)
        for dep in tasks[task]: visit(dep)
        visiting.remove(task)
        visited.add(task)
        order.append(task)
    for task in tasks: visit(task)
    return order


def grant(active, request, actor, lead):
    """Pure checker, not a remote lock. Caller must be the single serialized lead."""
    if not lead or actor != lead: raise ValueError('only configured lead may grant')
    if not isinstance(active, list): raise ValueError('active grants must be a list')
    for item in [*active, request]:
        if not isinstance(item, dict): raise ValueError('grant must be an object')
        for field in ('task', 'owner', 'machine', 'nonce'):
            if not isinstance(item.get(field), str) or not item[field].strip(): raise ValueError('missing ' + field)
        if type(item.get('revision')) is not int or item['revision'] < 1: raise ValueError('invalid revision')
        if not isinstance(item.get('scope'), list) or not item['scope']: raise ValueError('scope must be nonempty list')
        for path in item['scope']: normalized_path(path)
    for old in active:
        if old['task'] == request['task'] or old['nonce'] == request['nonce']:
            raise ValueError('task already reserved or duplicate nonce')
        if any(overlap(a, b) for a in old['scope'] for b in request['scope']): raise ValueError('scope conflict')
    return [*active, dict(request, state='granted')]


def reclaim(active, request, actor, lead, expected_revision, integration_fenced, reason):
    """Plan primary-owner takeover after remote integration fencing; never writes GitHub."""
    if not lead or actor != lead or request.get('owner') != lead:
        raise ValueError('Only primary integration lead may reclaim for itself')
    if integration_fenced is not True or not isinstance(reason,str) or not reason.strip():
        raise ValueError('Record integration fencing and reason before reclaim')
    if not isinstance(active,list): raise ValueError('active grants must be a list')
    grant([],request,actor,lead)
    for item in active: grant([],item,actor,lead)
    matches=[item for item in active if item['task']==request['task']]
    if len(matches)!=1: raise ValueError('Exactly one active task grant required')
    old=matches[0]
    if type(expected_revision) is not int or expected_revision != old['revision']:
        raise ValueError('Stale reclaim snapshot')
    if request['revision'] != old['revision']+1 or request['nonce']==old['nonce']:
        raise ValueError('New revision and nonce required')
    if sorted(map(normalized_path,request['scope'])) != sorted(map(normalized_path,old['scope'])):
        raise ValueError('Reclaim preserves scope; revise contract separately')
    remaining=[item for item in active if item['task']!=request['task']]
    return dict(active=grant(remaining,request,actor,lead),retired=dict(old,state='revoked',reason=reason),
                note='Publish revocation and grant serially; old deliveries cannot integrate')


def gate(e):
    errors = []
    if e.get('mode') not in ('SOLO', 'CO-OP'): errors.append('explicit valid mode required')
    if not isinstance(e.get('author'), str) or not e['author'].strip(): errors.append('author missing')
    for key in ('tested_head', 'reviewed_head'):
        if not e.get('head') or e.get(key) != e['head']: errors.append(key + ' is stale/missing')
    for key in ('tested_base', 'reviewed_base'):
        if not e.get('base') or e.get(key) != e['base']: errors.append(key + ' is stale/missing')
    if not isinstance(e.get('reviewer'), str) or not e['reviewer'].strip():
        errors.append('independent review required')
    if any(not isinstance(e.get(k),str) or not e[k].strip() for k in ('implementer_session','reviewer_session')):
        errors.append('implementation and independent review session IDs required')
    elif e['implementer_session'] == e['reviewer_session']:
        errors.append('reviewer must use an independent context')
    if e.get('review_result') != 'passed': errors.append('independent review not passed')
    if e.get('mode') == 'CO-OP' and (not e.get('grant_revision') or e.get('grant_revision') != e.get('current_revision')):
        errors.append('stale or missing grant')
    for key in ('dependencies_merged', 'conversations_resolved', 'mergeable', 'protection_verified'):
        if e.get(key) is not True: errors.append(key + ' not verified')
    if e.get('blockers') != []: errors.append('blockers unresolved or unknown')
    if not e.get('required_checks'): errors.append('required checks not configured')
    for key in e.get('required_checks', []):
        if e.get('checks', {}).get(key) != 'success': errors.append(key + ' not successful')
    return errors


def usage(events):
    seen, groups = {}, {}
    total, unknown = 0, 0
    for e in events:
        identity = e['event_id']
        if identity in seen:
            if seen[identity] != e: raise ValueError('conflicting duplicate event')
            continue
        seen[identity] = e
        if e.get('counter_mode') != 'delta': raise ValueError('normalize counters to delta before import')
        values = [e.get(k) for k in ('input_tokens', 'output_tokens', 'cached_tokens')]
        if any(v is not None and (type(v) is not int or v < 0) for v in values): raise ValueError('invalid tokens')
        inp, out, cache = values
        if inp is not None and cache is not None and cache > inp: raise ValueError('cache exceeds input')
        key = '/'.join(e.get(k, 'unknown') for k in ('role', 'model', 'effort'))
        g = groups.setdefault(key, dict(known_total_tokens=0, unknown_events=0, events=0))
        g['events'] += 1
        if inp is None or out is None:
            unknown += 1
            g['unknown_events'] += 1
        else:
            total += inp + out
            g['known_total_tokens'] += inp + out
    return dict(known_total_tokens=total, unknown_events=unknown, groups=groups,
                note='Partial measured deltas; no conversion to subscription quota or currency')


def reputation(events, now=None):
    now = now or datetime.now(timezone.utc)
    buckets = defaultdict(dict)
    for e in events:
        date = datetime.fromisoformat(e['timestamp'].replace('Z', '+00:00'))
        if date.tzinfo is None: raise ValueError('UTC offset required')
        if not now-timedelta(days=30) <= date <= now: continue
        if e['result'] != 'passed' and e.get('failure_class') not in ('implementation', 'reasoning', 'complexity'): continue
        key = tuple(e.get(k, 'unknown') for k in ('project', 'task_class', 'model', 'effort', 'policy_version'))
        prev = buckets[key].get(e['task'])
        if not prev or date >= prev[0]: buckets[key][e['task']] = (date, e['result'] == 'passed')
    result = []
    for key, tasks in sorted(buckets.items()):
        passed = sum(v[1] for v in tasks.values())
        n = len(tasks)
        cheap = key[2] == 'gpt-5.6-luna' and key[3] in ('low', 'medium')
        proposal = 'review_promotion' if n-passed >= 3 else ('review_low_risk_probe' if cheap and passed >= 5 and n == passed else None)
        result.append(dict(zip(('project','task_class','model','effort','policy_version'), key), tasks=n,
                           passed=passed, failed=n-passed, smoothed_success=(passed+1)/(n+2),
                           proposal=proposal, policy_mutated=False))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['decide', 'usage', 'reputation', 'dag', 'gate', 'grant', 'reclaim', 'validate'])
    p.add_argument('input', help='JSON file, or installed project directory for validate')
    p.add_argument('--preset', choices=['balanced', 'critical'], default='balanced')
    p.add_argument('--gate', action='store_true', help='also execute configured product checks')
    a = p.parse_args()
    try:
        if a.command == 'validate':
            from installer import validate_project
            result = validate_project(Path(a.input), run_checks=a.gate)
        else:
            data = json.loads(Path(a.input).read_text(encoding='utf-8-sig'))
            if a.command == 'decide': result = decide(data, a.preset)
            elif a.command == 'grant': result = grant(**data)
            elif a.command == 'reclaim': result = reclaim(**data)
            else: result = {'usage':usage, 'reputation':reputation, 'dag':check_dag, 'gate':gate}[a.command](data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if a.command == 'gate' and result else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())

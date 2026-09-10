"""Serialized GitHub Actions coordinator. Run only in the shared concurrency group."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
from multicontroller import grant, normalized_path, overlap, gate


def operator(team,login):
    matches=[o['id'] for o in team['operators'] if o['github_login'].casefold()==login.casefold()]
    if len(matches)!=1 or matches[0] not in team.get('integration_operators',[]):
        raise ValueError('Actor is not an authorized integration operator')
    return matches[0]


def fingerprint(request,login):
    return hashlib.sha256(json.dumps([login.casefold(),request],sort_keys=True).encode()).hexdigest()


def finish(store,state,request,fp,result):
    state['version']+=1
    result=dict(result,version=state['version'])
    receipts=state['receipts']
    receipts[request['id']]={'fingerprint':fp,'result':result}
    while len(receipts)>50: del receipts[next(iter(receipts))]
    state['pending']=None
    store.save(state)
    return result


def run(store,team,login,request):
    actor=operator(team,login)
    if not isinstance(request,dict) or not isinstance(request.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request['id']):
        raise ValueError('Unique request id required')
    state=store.read()
    if type(state.get('version')) is not int or not isinstance(state.get('active'),list) or not isinstance(state.get('receipts'),dict):
        raise ValueError('Invalid control state; do not reset it')
    fp=fingerprint(request,login)
    if request['id'] in state['receipts']:
        receipt=state['receipts'][request['id']]
        if fp!=receipt['fingerprint']: raise ValueError('Request ID reused with different actor/payload')
        return receipt['result']
    if state.get('pending'):
        if request.get('operation')!='recover' or request.get('pending_id')!=state['pending']['request']['id']:
            raise ValueError('Resolve pending integration before another state change')
        return complete_merge(store,team,state)
    if type(request.get('expected_version')) is not int or request['expected_version']!=state['version']:
        raise ValueError('Stale state version; re-read, reconcile and use a new request ID')
    task=request['task']
    grant([],task,actor,actor)  # strict shape/path validation, not authority delegation
    operation=request['operation']
    if operation not in ('claim','reclaim','release','merge'): raise ValueError('Unsupported coordinator operation')
    active=state['active']
    old=next((x for x in active if x['task']==task['task']),None)
    revisions=state.setdefault('revisions',{})
    if operation in ('claim','reclaim'):
        if task['owner']!=actor: raise ValueError('Cannot impersonate another operator')
        if task['revision']!=revisions.get(task['task'],0)+1: raise ValueError('Next task revision required')
        if operation=='reclaim':
            if not old or not request.get('reason'): raise ValueError('Active grant and reclaim reason required')
            if sorted(map(normalized_path,task['scope']))!=sorted(map(normalized_path,old['scope'])):
                raise ValueError('Reclaim cannot expand scope')
            for key in set(task)|set(old):
                if key not in ('owner','machine','nonce','revision','state') and task.get(key)!=old.get(key):
                    raise ValueError('Reclaim must preserve contract and dependencies')
            if task['nonce']==old['nonce']: raise ValueError('New nonce required')
            active=[x for x in active if x['task']!=task['task']]
        state['active']=grant(active,task,actor,actor)
        revisions[task['task']]=task['revision']
    elif operation=='release':
        if not old or old['owner']!=actor or any(old[k]!=task[k] for k in ('revision','nonce','machine')):
            raise ValueError('Only current owner may release current grant')
        state['active']=[x for x in active if x['task']!=task['task']]
    else:
        if not old or any(old[k]!=task[k] for k in ('owner','revision','nonce','machine')):
            raise ValueError('Merge requires the current grant')
        verify_merge(store,team,request,old)
        state['pending']={'request':copy.deepcopy(request),'fingerprint':fp}
        store.save(state)  # durable intent before external effect; all other operations are fenced
        return complete_merge(store,team,state)
    return finish(store,state,request,fp,dict(operation=operation,task=task['task'],revision=task['revision']))


def verify_merge(store,team,request,old):
    n=request.get('pr')
    if type(n) is not int or n<1: raise ValueError('PR number required')
    pr=store.pr(n)
    if pr['state']!='open' or pr['merged'] or pr['draft']: raise ValueError('PR must be open, unmerged and ready')
    if pr['base']['ref']!=team['integration_branch'] or store.branch(pr['base']['ref']).get('protected') is not True:
        raise ValueError('Protected integration branch required')
    e=dict(request['evidence'],mode='CO-OP',head=pr['head']['sha'],base=pr['base']['sha'],
           grant_revision=old['revision'],current_revision=old['revision'],mergeable=pr['mergeable'],required_checks=team['required_checks'])
    errors=gate(e)
    if errors: raise ValueError('; '.join(errors))
    for item in store.files(n):
        for p in [item['filename']]+([item['previous_filename']] if 'previous_filename' in item else []):
            if not any(overlap(p,s) for s in old['scope']): raise ValueError('PR file outside granted scope: '+p)
    for dep in old.get('depends_on',[]):
        merged=store.pr(dep)
        if not merged.get('merged') or not store.contains(merged['merge_commit_sha'],pr['base']['sha']):
            raise ValueError('Dependency must be merged and present in base')
    return pr


def complete_merge(store,team,state):
    pending=state['pending'];request=pending['request']
    pr=store.pr(request['pr'])
    if not pr['merged'] and pr['state']=='closed':
        return finish(store,state,request,pending['fingerprint'],{'operation':'merge','aborted':True})
    if pr['head']['sha']!=request['evidence']['head']: raise ValueError('Pending PR head changed; close unmerged PR then recover to abort')
    if not pr['merged']:
        old=next(x for x in state['active'] if x['task']==request['task']['task'])
        verified=verify_merge(store,team,request,old)
        outcome=store.merge(request['pr'],verified['head']['sha'])
        if not outcome.get('merged'): raise ValueError('GitHub did not merge; pending intent retained for recovery')
        sha=outcome['sha']
    else: sha=pr['merge_commit_sha']
    state['active']=[x for x in state['active'] if x['task']!=request['task']['task']]
    return finish(store,state,request,pending['fingerprint'],{'operation':'merge','pr':request['pr'],'sha':sha})


class GitHub:
    def __init__(self,repo,issue,token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('Invalid repository')
        if type(issue) is not int or issue<1: raise ValueError('Configure coordination.control_issue first')
        self.repo,self.issue,self.token=repo,issue,token
    def api(self,method,path,data=None):
        req=urllib.request.Request('https://api.github.com/repos/'+self.repo+path,
            data=None if data is None else json.dumps(data).encode(),method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30) as response: return json.load(response)
    def read(self):
        issue=self.api('GET',f'/issues/{self.issue}')
        if issue.get('pull_request') or issue.get('state')!='open': raise ValueError('Control Issue must remain open')
        return json.loads(issue['body'])
    def save(self,state):
        body=json.dumps(state,ensure_ascii=True)
        if len(body)>60000: raise ValueError('Control Issue near size limit; archive completed revision tombstones with reviewed migration')
        self.api('PATCH',f'/issues/{self.issue}',{'body':body})
    def pr(self,n): return self.api('GET',f'/pulls/{n}')
    def branch(self,name):
        from urllib.parse import quote
        return self.api('GET','/branches/'+quote(name,safe=''))
    def files(self,n):
        results=[]
        for page in range(1,31):
            batch=self.api('GET',f'/pulls/{n}/files?per_page=100&page={page}')
            results.extend(batch)
            if len(batch)<100:return results
        raise ValueError('PR file list at API limit; split PR before integration')
    def contains(self,ancestor,head):
        return self.api('GET',f'/compare/{ancestor}...{head}')['status'] in ('ahead','identical')
    def merge(self,n,sha):return self.api('PUT',f'/pulls/{n}/merge',{'sha':sha,'merge_method':'squash'})


def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    team=json.loads(Path('AGENT_TEAM.yml').read_text())
    default=event['repository']['default_branch']
    if os.environ.get('GITHUB_REF')!='refs/heads/'+default: raise ValueError('Run workflow only from default branch')
    if team.get('mode')!='CO-OP': raise ValueError('Coordinator is for CO-OP projects')
    store=GitHub(os.environ['GITHUB_REPOSITORY'],team['coordination']['control_issue'],os.environ['GH_TOKEN'])
    result=run(store,team,os.environ['GITHUB_ACTOR'],json.loads(event['inputs']['request']))
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()

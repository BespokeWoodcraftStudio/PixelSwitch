import pickle
from collections import defaultdict,Counter
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
fails=defaultdict(list)  # account -> times of failing calls on api.anthropic.com (usage 401/429 + profile 401)
for r in reqs:
    if r['out'] in (401,429): fails[r['acct']].append((r['t'],'u'+str(r['out'])))
for p in prof:
    if 'HTTP 401' in p[1] and p[2]: fails[p[2]].append((p[0],'p401'))
for a in fails: fails[a].sort()
# for each usage request that is 401 or 429 or 200, count prior failures (usage+profile) within W minutes
def stats(W, include_profile=True, include_429=True):
    tab=Counter()
    for r in reqs:
        if r['out'] not in (200,401,429): continue
        a=r['acct']
        n=sum(1 for (t,k) in fails[a] if 0<(r['t']-t).total_seconds()<=W*60 and (include_profile or k!='p401') and (include_429 or k!='u429'))
        tab[(min(n,9),r['out'])]+=1
    return tab
for W in (30,60,90):
    for inc in (True,False):
        tab=stats(W,inc,False)
        print(f'W={W} profile={inc} (prior failures excl 429s):')
        for n in range(10):
            row={o:tab.get((n,o),0) for o in (200,401,429)}
            if any(row.values()): print('   n=%d'%n,row)

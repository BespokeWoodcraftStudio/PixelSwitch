import pickle
from collections import defaultdict,Counter
from datetime import timedelta
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
by=defaultdict(list)
for r in reqs: by[r['acct']].append(r)
p401=[p for p in prof if 'HTTP 401' in p[1]]
print('profile 401 total',len(p401),Counter(p[2] for p in p401))
import datetime as D
a=D.datetime(2026,10,13,1,0,tzinfo=D.timezone.utc); b=D.datetime(2026,10,15,4,20,tzinfo=D.timezone.utc)
print('cPV-active profile 401 in 10-13 01:00..10-15 04:20:',sum(1 for p in p401 if p[2]=='cPV' and a<=p[0]<=b))
# first request after each park for accounts that were active at the 429
res=[]
for acc in by:
    seq=by[acc]
    for i,r in enumerate(seq):
        if r['out']!=429: continue
        if i+1>=len(seq): continue
        nx=seq[i+1]
        if nx['active']!=acc: continue
        pf=sum(1 for p in p401 if p[2]==acc and 0<(nx['t']-p[0]).total_seconds()<=3600)
        gap=(nx['t']-r['t']).total_seconds()/60
        res.append((nx['t'],acc,nx['out'],pf,round(gap)))
for x in sorted(res): print(x[0].strftime('%m-%d %H:%M'),x[1],'first-after-park=',x[2],'prof401_prev60=',x[3],'gap_min=',x[4])
hi=[x for x in res if x[3]>=3]; lo=[x for x in res if x[3]<=2]
print('>=3 prof:',len(hi),Counter(x[2] for x in hi),' <=2 prof:',len(lo),Counter(x[2] for x in lo))

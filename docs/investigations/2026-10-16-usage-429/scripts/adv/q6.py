import pickle,re
from collections import defaultdict
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
ID={'36EFDE3A':'PV','8731B4AF':'GM','7B279285':'BWS','3A3CCE29':'cPV','CB7797E5':'cBWS'}
by=defaultdict(list)
for r in reqs: by[r['acct']].append(r)
p401=[p for p in prof if 'HTTP 401' in p[1]]
for (t,lv,c,f,m) in lines:
    if 'Delegated refresh completed' in m:
        # find next requests of active acct
        # active at t
        act=None
        for r in reqs:
            if r['t']>=t: act=r['acct'];break
        seq=[r for r in by[act] if r['t']>=t][:4]
        print(t.strftime('%m-%d %H:%M:%S'),act,' next:',' '.join(f"{r['t'].strftime('%H:%M')}:{r['out']}" for r in seq))

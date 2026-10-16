import pickle
from collections import defaultdict
from datetime import datetime,timezone,timedelta
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
T=lambda h,m,d=16:datetime(2026,10,d,h,m,tzinfo=timezone.utc)
# reconstruct parks
parks={}
cyc=defaultdict(list)
for r in reqs:
    if T(5,0)<=r['t']<=T(14,10):
        key=r['t'].replace(second=0,microsecond=0)
        cyc[key].append(r)
for k in sorted(cyc):
    rs=cyc[k]
    parked=[a for a,u in parks.items() if u>k]
    s=' '.join(f"{r['acct']}:{r['out']}{'*' if r['acct']==r['active'] else ''}" for r in rs)
    print(k.strftime('%H:%M'),'active=',rs[0]['active'],'| parked:',','.join(sorted(parked)) or '-','|',s)
    for r in rs:
        if r['out']==429: parks[r['acct']]=r['t']+timedelta(seconds=3600)

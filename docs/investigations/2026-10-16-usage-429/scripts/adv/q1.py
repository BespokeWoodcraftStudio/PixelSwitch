import pickle,statistics
from collections import Counter,defaultdict
from datetime import timedelta
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
# latency by outcome
lat=defaultdict(list)
for r in reqs:
    if r['lat'] is not None: lat[r['out']].append(r['lat'])
for k,v in lat.items(): print(k,len(v),'median',round(statistics.median(v)),'p10',round(sorted(v)[len(v)//10]),'p90',round(sorted(v)[9*len(v)//10]),'max',round(max(v)))
# 429 timeline
r429=[r for r in reqs if r['out']==429]
print(len(r429))
by=defaultdict(list)
for r in reqs: by[r['acct']].append(r)
for r in r429:
    a=r['acct']; seq=by[a]; k=seq.index(r)
    last200=None
    for x in reversed(seq[:k]):
        if x['out']==200: last200=x;break
    n401=sum(1 for x in seq[:k] if x['out']==401 and (last200 is None or x['t']>last200['t']))
    prev=seq[k-1] if k else None
    win=[x for x in seq[:k] if (r['t']-x['t']).total_seconds()<=3600]
    pf=sum(1 for p in prof if 'HTTP 401' in p[1] and p[2]==a and 0<(r['t']-p[0]).total_seconds()<=3600)
    print(r['t'].strftime('%m-%d %H:%M:%S'),a,'active=',r['active'],'lat=%d'%r['lat'],'last200=',last200['t'].strftime('%m-%d %H:%M') if last200 else None,'401s_since=',n401,'prev=',prev['out'] if prev else None, prev['t'].strftime('%H:%M:%S') if prev else '', 'win60=',Counter(x['out'] for x in win).most_common(),'prof401_60m=',pf)

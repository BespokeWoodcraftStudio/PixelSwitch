import pickle
from collections import Counter,defaultdict
from datetime import datetime,timezone
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
by=defaultdict(list)
for r in reqs: by[r['acct']].append(r)
cut=datetime(2026,10,16,6,0,tzinfo=timezone.utc)
for a in ['PV','GM']:
    seq=by[a]
    bad=[x for x in seq if x['out']==401]
    # max 401s in any 60 min window, before cut and after
    for label,cond in [('before 10-16 06:00',lambda x:x['t']<cut),('after',lambda x:x['t']>=cut)]:
        b=[x for x in bad if cond(x)]
        mx=0;where=None
        for i,x in enumerate(b):
            n=sum(1 for y in b[i:] if (y['t']-x['t']).total_seconds()<3600)
            if n>mx: mx=n;where=x['t']
        mx2=0
        for i,x in enumerate(b):
            n=sum(1 for y in b[i:] if (y['t']-x['t']).total_seconds()<4*3600)
            mx2=max(mx2,n)
        gaps=[(b[i+1]['t']-b[i]['t']).total_seconds()/60 for i in range(len(b)-1)]
        gaps=[g for g in gaps if g<40]
        import statistics
        print(a,label,'n401=',len(b),'max/60min=',mx,'at',where,'max/4h=',mx2,'median gap(min, <40)=',round(statistics.median(gaps),1) if gaps else None)
    # list windows with >=4 401s in 60 min before cut
    b=[x for x in bad if x['t']<cut]
    hits=[]
    for i,x in enumerate(b):
        w=[y for y in b[i:] if (y['t']-x['t']).total_seconds()<3600]
        if len(w)>=4: hits.append((x['t'].strftime('%m-%d %H:%M'),len(w)))
    print(a,'windows>=4 401 in 60min before cut:',len(hits),hits[:20])
# first/last 401 for PV, GM
for a in ['PV','GM']:
    seq=by[a]
    print(a,'first req',seq[0]['t'],'outcomes per day',sorted(Counter((x['t'].strftime('%m-%d'),x['out']) for x in seq).items()))

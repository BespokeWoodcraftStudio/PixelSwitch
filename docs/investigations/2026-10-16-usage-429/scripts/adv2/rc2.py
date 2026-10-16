import json
from datetime import datetime, timedelta
d=json.load(open('../syn/ev.json'))
U=[(datetime.fromisoformat(e[4][:23]),e[2],e[3]) for e in d if e[0]=='usage']
for acc in ['PV','GM']:
    ev=[(t,s) for t,a,s in U if a==acc]
    # max # of 401s in any window W ending at a request that was NOT followed by 429 within window
    for W in [45,60,75,90]:
        best_no429=(0,None); best_429=[]
        for i,(t,s) in enumerate(ev):
            win=[x for x in ev[:i] if t-timedelta(minutes=W)<=x[0]<t]
            n401=sum(1 for x in win if x[1]==401)
            n429=sum(1 for x in win if x[1]==429)
            if s==429 and n429==0: best_429.append((t.strftime('%m-%d %H:%M'),n401))
            if s==401 and n429==0 and n401>best_no429[0]: best_no429=(n401,t.strftime('%m-%d %H:%M'))
        print(acc,'W',W,'max prior 401s before a non-429 req:',best_no429,'| onset429s:',best_429)
    # per-day: count and min gap between consecutive requests
    from collections import defaultdict
    byday=defaultdict(list)
    for t,s in ev: byday[t.date()].append((t,s))
    for day,l in sorted(byday.items()):
        gaps=[(l[i][0]-l[i-1][0]).total_seconds()/60 for i in range(1,len(l))]
        c={}
        for _,s in l: c[s]=c.get(s,0)+1
        print(' ',acc,day,len(l),c,'median gap',sorted(gaps)[len(gaps)//2] if gaps else None,'min gap',round(min(gaps),1) if gaps else None)

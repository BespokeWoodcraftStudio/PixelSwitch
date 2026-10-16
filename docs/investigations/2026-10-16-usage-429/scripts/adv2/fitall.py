import json
from datetime import datetime
d=json.load(open('../syn/ev.json'))
ev=[]
for e in d:
    t=datetime.fromisoformat(e[4][:23]).timestamp()
    if e[0]=='usage' and e[3] in (200,401,429): ev.append((t,e[2],'u',e[3],e[4]))
    if e[0]=='profile' and e[3]==401 and e[2]: ev.append((t,e[2],'p',401,e[4]))
ev.sort()
def run(K,count_profile,W=3600):
    hist={}; bad=[]; n429=0; tp=0
    for t,a,k,s,ts in ev:
        h=hist.setdefault(a,[])
        if k=='u':
            start=t-(t%W); el=t-start
            prev=sum(1 for x in h if start-W<=x<start); cur=sum(1 for x in h if start<=x<t)
            r=prev*(W-el)/W+cur+1
            pred=429 if r>=K else (200 if s==200 else 401)
            if s==429: n429+=1
            if (pred==429)!=(s==429): bad.append((ts[5:19],a,s,round(r,2)))
            elif s==429: tp+=1
            if s==200: hist[a]=[]
            elif s==401: h.append(t)
        elif count_profile: h.append(t)
    return n429,tp,bad
for cp in (False,True):
    n,tp,bad=run(4,cp)
    print('count_profile',cp,'429s',n,'predicted correctly',tp,'mismatches',len(bad))
    for b in bad[:40]: print('  ',b)

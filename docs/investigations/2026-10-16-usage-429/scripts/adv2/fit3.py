import json
from datetime import datetime, timedelta
d=json.load(open('../syn/ev.json'))
def seq(acc,start):
    return [(datetime.fromisoformat(e[4][:23]),e[3]) for e in d if e[0]=='usage' and e[2]==acc and e[4]>=start and e[3] in (401,429)]
S={'PV':seq('PV','2026-10-12T15:53'),'GM':seq('GM','2026-10-10T19:01')}
def sim(ev,Wmin,K,incl_current):
    W=Wmin*60; hist=[]; bad=[]
    for t,s in ev:
        ts=t.timestamp(); start=ts-(ts%W); el=ts-start
        prev=sum(1 for x in hist if start-W<=x<start); cur=sum(1 for x in hist if start<=x<ts)
        rate=prev*(W-el)/W+cur+(1 if incl_current else 0)
        pred=429 if rate>K else 401
        if pred!=s: bad.append((t.strftime('%m-%d %H:%M'),s,pred,round(rate,2)))
        if s==401: hist.append(ts)
    return bad
out=[]
for W in (10,20,30,60,120):
  for K10 in range(10,80):
    K=K10/10
    for ic in (True,):
      b=sum((sim(v,W,K,ic) for v in S.values()),[])
      out.append((len(b),W,K,b[:6]))
out.sort(key=lambda x:x[0])
for o in out[:10]: print(o)

import json
from datetime import datetime, timedelta
d=json.load(open('../syn/ev.json'))
def seq(acc,start):
    return [(datetime.fromisoformat(e[4][:23]),e[3]) for e in d if e[0]=='usage' and e[2]==acc and e[4]>=start and e[3] in (401,429)]
S={'PV':seq('PV','2026-10-12T15:53'),'GM':seq('GM','2026-10-10T19:01')}
def sim(ev,W,K,count429,cleared):
    hist=[]; bad=[]; last429=None
    for t,s in ev:
        lo=t-timedelta(minutes=W)
        if cleared and last429: lo=max(lo,last429)
        n=sum(1 for (x,y) in hist if x>=lo and (y==401 or (count429 and y==429)))
        pred=429 if n>=K else 401
        if pred!=s: bad.append((t.strftime('%m-%d %H:%M'),s,pred,n))
        hist.append((t,s))
        if s==429: last429=t
    return bad
out=[]
for W in range(30,241,5):
  for K in range(2,9):
    for c4 in (False,True):
      for cl in (False,True):
        b=sum((sim(v,W,K,c4,cl) for v in S.values()),[])
        out.append((len(b),W,K,c4,cl,b[:5]))
out.sort(key=lambda x:(x[0],x[1]))
for o in out[:15]: print(o)

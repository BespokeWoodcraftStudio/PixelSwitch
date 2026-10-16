import json
from datetime import datetime
d=json.load(open('../syn/ev.json'))
def seq(acc,start):
    return [(datetime.fromisoformat(e[4][:23]),e[3]) for e in d if e[0]=='usage' and e[2]==acc and e[4]>=start and e[3] in (401,429)]
S={'PV':seq('PV','2026-10-12T15:53'),'GM':seq('GM','2026-10-10T19:01')}
def sim(ev,C,P,reset):
    # token bucket: capacity C, +1 per P minutes; each 401 costs 1; when <1 -> predict 429, block 3600s
    b=C; last=None; blocked_until=None; ok=0; bad=[]
    for t,s in ev:
        if last is not None: b=min(C,b+(t-last).total_seconds()/60/P)
        last=t
        if blocked_until and t<blocked_until: pred=429
        else:
            if blocked_until and reset: b=C
            blocked_until=None
            if b<1-1e-9: pred=429; blocked_until=t.timestamp() and t.replace() ; 
            else: pred=401; b-=1
        if pred==429 and blocked_until is None: blocked_until=t
        if pred==429:
            from datetime import timedelta
            if blocked_until==t or blocked_until is None: blocked_until=t+timedelta(seconds=3600)
        if pred==s: ok+=1
        else: bad.append((t.strftime('%m-%d %H:%M'),s,pred))
    return ok,bad
res=[]
for C in [2,3,4,5,6,8]:
  for P in range(10,41,1):
    for reset in (True,False):
      tot=0;bads=[]
      for k,ev in S.items():
        ok,bad=sim(ev,C,P,reset); tot+=ok; bads+= [(k,)+b for b in bad]
      res.append((len(bads),C,P,reset,bads[:6]))
res.sort(key=lambda x:x[0])
n=sum(len(v) for v in S.values()); print('events',n, {k:len(v) for k,v in S.items()}, '429s', sum(1 for v in S.values() for _,s in v if s==429))
for r in res[:12]: print(r)

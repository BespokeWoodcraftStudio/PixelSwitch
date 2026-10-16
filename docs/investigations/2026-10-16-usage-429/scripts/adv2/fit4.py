exec(open('fit3.py').read().split('out=[]')[0])
for K in (3.95,3.97,3.99,3.999,4.0):
    b=sum((sim(v,60,K,True) for v in S.values()),[])
    print(K,len(b),b[:8])
# distribution of max rate among 401s
import collections
for k,v in S.items():
    W=3600;hist=[];rates=[]
    for t,s in v:
        ts=t.timestamp(); start=ts-(ts%W); el=ts-start
        prev=sum(1 for x in hist if start-W<=x<start); cur=sum(1 for x in hist if start<=x<ts)
        r=prev*(W-el)/W+cur+1; rates.append((round(r,3),s,t.strftime('%m-%d %H:%M:%S')))
        if s==401: hist.append(ts)
    print(k,'max rate on a 401:',max(x for x in rates if x[1]==401),' min rate on a 429:',min(x for x in rates if x[1]==429))

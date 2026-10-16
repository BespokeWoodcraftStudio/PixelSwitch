import pickle
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
lines,reqs,prof=pickle.load(open('d.pkl','rb'))
by=defaultdict(list)
for r in reqs: by[r['acct']].append(r)
for a in ['PV','GM']:
    seq=[x for x in by[a] if x['out'] in (401,429)]
    # start of dead period: after last 200
    last200=max(x['t'] for x in by[a] if x['out']==200 and x['t']<datetime(2026,10,16,13,0,tzinfo=timezone.utc))
    seq=[x for x in seq if x['t']>last200]
    # for each request: trailing 60 / 75 / 90 minute inclusive counts of 401/429 requests; span of last 4 and last 5
    rows=[]
    for i,x in enumerate(seq):
        c60=sum(1 for y in seq[:i+1] if (x['t']-y['t']).total_seconds()<=3600)
        c90=sum(1 for y in seq[:i+1] if (x['t']-y['t']).total_seconds()<=5400)
        c120=sum(1 for y in seq[:i+1] if (x['t']-y['t']).total_seconds()<=7200)
        s4=(x['t']-seq[i-3]['t']).total_seconds()/60 if i>=3 else None
        s5=(x['t']-seq[i-4]['t']).total_seconds()/60 if i>=4 else None
        rows.append((x,c60,c90,c120,s4,s5))
    pre=[r for r in rows if r[0]['t']<datetime(2026,10,16,6,50,tzinfo=timezone.utc)]
    post=[r for r in rows if r[0]['t']>=datetime(2026,10,16,6,50,tzinfo=timezone.utc)]
    print(a,'dead since',last200,'pre n=',len(pre),'post n=',len(post))
    print(' pre: max c60',max(r[1] for r in pre),'max c90',max(r[2] for r in pre),'max c120',max(r[3] for r in pre),'min span4',round(min(r[4] for r in pre if r[4]),1),'min span5',round(min(r[5] for r in pre if r[5]),1))
    print(' pre: count of requests with c60>=5:',sum(1 for r in pre if r[1]>=5),' c60==4:',sum(1 for r in pre if r[1]==4),' c90>=6:',sum(1 for r in pre if r[2]>=6), 'c120>=7', sum(1 for r in pre if r[3]>=7))
    for r in post:
        x=r[0]; print('  ',x['t'].strftime('%m-%d %H:%M:%S'),x['out'],'c60=',r[1],'c90=',r[2],'c120=',r[3],'span4=',round(r[4],1),'span5=',round(r[5],1))
    # top pre rows by c90
    top=sorted(pre,key=lambda r:(-r[2],-r[1]))[:8]
    for r in top: print('   pre-top',r[0]['t'].strftime('%m-%d %H:%M'),r[0]['out'],'c60',r[1],'c90',r[2],'c120',r[3],'span4',round(r[4] or 0,1),'span5',round(r[5] or 0,1))

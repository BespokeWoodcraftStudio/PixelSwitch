import re, os, json, pickle
from datetime import datetime, timezone
L=os.path.expanduser('~/Library/Logs/')
files=[L+'PixelSwitch-app.log.2',L+'PixelSwitch-app.log.1',L+'PixelSwitch-app.log']
ID={'36EFDE3A-86E8-40F5-9B88-404356E5645B':'PV','8731B4AF-6F4A-4B03-8E67-58B8FE8E9182':'GM','7B279285-010A-4525-A369-080598767275':'BWS','3A3CCE29-8FFA-4519-8DAA-A7E985F656D7':'cPV','CB7797E5-5257-402B-80D6-ADAE1220D368':'cBWS'}
EM={'ahmed@pixelventures.ai':'PV','ahamade@gmail.com':'GM','ahmed@bespokewoodcraftstudio.com':'BWS','claude@pixelventures.ai':'cPV','claude@bespokewoodcraftstudio.com':'cBWS'}
rx=re.compile(r'^\[(\S+)Z\] \[(\w+)\] \[(\w+)\] (?:\[(\w+)\] )?(.*)$')
lines=[]
for f in files:
    for ln in open(f,errors='replace'):
        m=rx.match(ln.rstrip('\n'))
        if not m: continue
        t=datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
        lines.append((t,m.group(2),m.group(3),m.group(4) or '',m.group(5)))
print(len(lines), lines[0][0], lines[-1][0])
def acct(msg):
    for k,v in ID.items():
        if k in msg: return v
    for k,v in EM.items():
        if k in msg: return v
    return None
reqs=[]; prof=[]
active=None
for i,(t,lv,comp,fn,msg) in enumerate(lines):
    if fn=='diagnose' and msg.startswith('Accounts:'):
        m=re.search(r'active: (\S+)',msg); active=EM.get(m.group(1)) if m else None
    if fn=='accountIdentity' and 'Profile lookup failed' in msg:
        prof.append((t,msg,active))
    if fn=='getUsageLimits' and 'REQUEST URL' in msg:
        out=None; ac=None; lat=None; ra=None
        for j in range(i+1,min(i+60,len(lines))):
            t2,lv2,c2,f2,m2=lines[j]
            if out is None and f2=='getUsageLimits':
                if 'REQUEST URL' in m2: break
                if lv2=='INFO' and 'session=' in m2: out=200
                elif lv2=='ERROR':
                    mm=re.search(r'HTTP (\d+)',m2); out=int(mm.group(1)) if mm else 'noreply'
                lat=(t2-t).total_seconds()*1000
            if f2=='getUsageLimits' and 'Retry-After' in m2:
                ra=m2
            if f2 in('fetchUsage','retryUsage','autoSwitch','switchTo') and lv2!='DEBUG':
                a=acct(m2)
                if a: ac=a; break
                if 'Delegated refresh' in m2: continue
        reqs.append(dict(t=t,out=out,acct=ac,lat=lat,ra=ra,active=active,i=i))
pickle.dump((lines,reqs,prof),open('d.pkl','wb'))
from collections import Counter
print(Counter((r['acct'],r['out']) for r in reqs))
print('unattributed',sum(1 for r in reqs if r['acct'] is None))
print('profile fails',len(prof),Counter(p[2] for p in prof))

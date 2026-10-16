import re, sys, json, os
from datetime import datetime
L=os.path.expanduser('~/Library/Logs/')
files=[L+'PixelSwitch-app.log.2',L+'PixelSwitch-app.log.1',L+'PixelSwitch-app.log']
ID={'36EFDE3A-86E8-40F5-9B88-404356E5645B':'PV','8731B4AF-6F4A-4B03-8E67-58B8FE8E9182':'GM','7B279285-010A-4525-A369-080598767275':'BWS','3A3CCE29-8FFA-4519-8DAA-A7E985F656D7':'cPV','CB7797E5-5257-402B-80D6-ADAE1220D368':'cBWS'}
EM={'ahmed@pixelventures.ai':'PV','ahamade@gmail.com':'GM','ahmed@bespokewoodcraftstudio.com':'BWS','claude@pixelventures.ai':'cPV','claude@bespokewoodcraftstudio.com':'cBWS'}
rx=re.compile(r'^\[(\S+?)\] \[(\w+)\] \[(\w+)\] (.*)$')
def acct(msg):
    for k,v in ID.items():
        if k in msg: return v
    # longest email first
    for k in sorted(EM,key=len,reverse=True):
        if k in msg: return EM[k]
    return None
events=[]  # (ts, kind, acct, status, extra)
active=None; pending=None; seen=set()
for f in files:
  for line in open(f,errors='replace'):
    m=rx.match(line.rstrip('\n'))
    if not m: continue
    ts,lvl,comp,msg=m.groups()
    if ts in seen and False: pass
    t=datetime.strptime(ts[:19],'%Y-%m-%dT%H:%M:%S')
    if '[diagnose] Accounts:' in msg:
        a=acct(msg.split('active:')[-1]); active=a
    if 'REQUEST URL' in msg and 'oauth/usage' in msg:
        pending={'t':t,'ts':ts,'status':None,'acct':None}
        continue
    if pending is not None and pending['status'] is None and '[getUsageLimits]' in msg:
        mm=re.search(r'HTTP (\d+)',msg)
        if mm: pending['status']=int(mm.group(1))
        elif 'session=' in msg: pending['status']=200
        elif 'No reply' in msg: pending['status']='net'
        continue
    if pending is not None and pending['status'] is not None and comp=='AppState' and pending['acct'] is None:
        a=acct(msg)
        if a:
            pending['acct']=a; pending['active']=active
            events.append(('usage',pending['t'],a,pending['status'],pending['ts'],active)); pending=None
            continue
    if '[accountIdentity] Profile lookup failed (HTTP' in msg:
        st=int(re.search(r'HTTP (\d+)',msg).group(1))
        events.append(('profile',t,active,st,ts,active))
    if 'refreshOAuth' in msg:
        if 'invalid_grant' in msg: events.append(('refresh',t,None,'invalid_grant',ts,active))
        elif 'refreshed' in msg: events.append(('refresh',t,None,'ok',ts,active))
    if '[reconcile]' in msg and 'backup updated from the live login' in msg:
        events.append(('reconcile',t,acct(msg),'upd',ts,active))
    if '[switchTo]' in msg or '[autoSwitch]' in msg:
        if 'Switch' in msg and ('complete' in msg.lower() or 'switched' in msg.lower()):
            events.append(('switch',t,acct(msg),msg[:120],ts,active))
json.dump([(k,t.isoformat(),a,s,ts,act) for k,t,a,s,ts,act in events],open(sys.argv[1],'w'))
from collections import Counter
c=Counter((e[0],e[2],e[3]) for e in events if e[0] in('usage','profile'))
for k in sorted(c,key=str): print(k,c[k])

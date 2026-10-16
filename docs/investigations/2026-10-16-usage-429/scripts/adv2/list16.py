import json
from datetime import datetime
d=json.load(open('../syn/ev.json'))
for e in d:
    if e[4]>='2026-10-15T19:40' and e[0] in ('usage','profile','switch','reconcile'):
        print(e[4][5:19], e[0][:4], e[2], e[3], 'act='+str(e[5]))

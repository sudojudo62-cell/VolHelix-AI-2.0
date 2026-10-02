import json, math, datetime as dt, numpy as np
from scipy.stats import norm, skew, kurtosis
from research.lab.venues import get_venue
DAY=86400000; BAR=4*3600000; BPD=6
d=json.load(open('research/summaries/trend_following.json')); run=d['run']; start,end=run['start_ms'],run['end_ms']
cell=next(x for x in d['results'] if x['strategy_id']=='tsmom_ls' and x['venue']=='dydx' and x['interval']=='4h' and x['assets']==['BTC'])
ad=get_venue('dydx')
rows=ad.candles('BTC','4h',start-90*DAY,end); fund=ad.funding('BTC',start-90*DAY,end)
ts=[r['ts'] for r in rows]; o=[r['open'] for r in rows]; c=[r['close'] for r in rows]; n=len(ts)
f=[0.0]*n
for e in fund:
    import bisect
    i=bisect.bisect_right(ts,e['ts'])-1
    if 0<=i<n and e['ts']<ts[i]+BAR: f[i]+=e['rate']
month=[dt.datetime.utcfromtimestamp(t/1000).strftime('%Y-%m') for t in ts]
cost=(5.0+4.0)/1e4
def run_cfg(L_days):
    L=L_days*BPD; W=[0.0]*n; cur=0.0
    for i in range(n):
        if i>=1 and month[i]!=month[i-1] and i>=L:
            cur=1.0 if c[i]/c[i-L]-1>0 else (-1.0 if c[i]/c[i-L]-1<0 else 0.0)
        W[i]=cur
    r=[0.0]*n
    for t in range(1,n):
        w1=W[t-1]; w2=W[t-2] if t>=2 else 0.0
        r[t]=w2*(o[t]/c[t-1]-1)+w1*(c[t]/o[t]-1)-abs(w1-w2)*cost-w1*f[t]
    return W,np.array(r)
rets={L:run_cfg(L)[1] for L in (30,90)}
tsa=np.array(ts); oos=[]
for fo in cell['folds']:
    a=int(np.searchsorted(tsa,fo['start_ts'])); b=int(np.searchsorted(tsa,fo['end_ts']))+1
    L=fo['params']['lookback_days']; seg=rets[L][a:b]; oos.append(seg)
    print(dt.datetime.utcfromtimestamp(fo['start_ts']/1000).date(),"L",L,"mine %.2f%%"%((np.prod(1+seg)-1)*100),"lab %.2f%%"%fo['oos_return_pct'])
r=np.concatenate(oos); tot=(np.prod(1+r)-1)*100
bpy=365*BPD; sr=r.mean()/r.std(ddof=1)
eq=np.cumprod(1+r); dd=(np.maximum.accumulate(eq)-eq).max()/np.maximum.accumulate(eq)[eq.argmax()] if False else ((np.maximum.accumulate(eq)-eq)/np.maximum.accumulate(eq)).max()*100
print("MINE total %.2f%% sharpe %.3f maxdd %.2f%% bars %d | LAB total %.2f%% sharpe %.3f dd %.2f%% bars %d"%(tot,sr*math.sqrt(bpy),dd,len(r),cell['oos_return_pct'],cell['oos_sharpe'],cell['oos_max_drawdown_pct'],cell['oos_bars']))
sk=skew(r); ku=kurtosis(r,fisher=False)
var=run['trial_sharpe_variance_by_interval']['4h']; N=run['n_trials']; EG=0.5772156649015329
sr0=math.sqrt(var)*((1-EG)*norm.ppf(1-1/N)+EG*norm.ppf(1-1/(N*math.e)))
z=(sr-sr0)*math.sqrt(len(r)-1)/math.sqrt(1-sk*sr+(ku-1)/4*sr*sr)
print("MINE DSR %.3f (sr0 %.4f) LAB DSR %.3f"%(norm.cdf(z),sr0,cell['deflated_sharpe_prob']))
# trades: position changes, monthly decisions
W,_=run_cfg(30); chg=sum(1 for i in range(1,n) if W[i]!=W[i-1] and ts[i]>=start); print("position changes in analysis window (L=30):",chg)
# B&H on same bars for context
a=int(np.searchsorted(tsa,cell['folds'][0]['start_ts'])); print("BTC close at OOS start %.0f end %.0f"%(c[a],c[-1]))
print("--- monthly decisions (L=30), analysis window")
for i in range(1,n):
    if month[i]!=month[i-1] and ts[i]>=start-35*DAY: print(dt.datetime.utcfromtimestamp(ts[i]/1000).date(),"weight",W[i],"close %.0f"%c[i], "30d mom %.1f%%"%((c[i]/c[i-180]-1)*100))

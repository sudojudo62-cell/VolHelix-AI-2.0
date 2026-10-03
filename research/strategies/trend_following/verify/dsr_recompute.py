import json, math, numpy as np
from scipy.stats import norm
from research.lab.venues import get_venue, INTERVAL_MS
from research.lab.data import build_market_data
from research.lab.engine import simulate, Costs
from research.lab.plugin import load_strategies
from research.lab.sweep import expand_grid
DAY=86400000
d=json.load(open('research/summaries/trend_following.json'))
run=d['run']; start,end=run['start_ms'],run['end_ms']
strats={s.id:s for s in load_strategies('trend_following')}
byiv={}
cache={}
n=0
for q in d['data_quality']:
    sid,vn,assets,iv=q['cell'].split('/'); aset=assets.split('+'); s=strats[sid]
    ad=get_venue(vn)
    md=build_market_data(ad,aset,iv,start-90*DAY,end,with_funding=ad.has_funding,allow_head_gap_bars=int(90*DAY/INTERVAL_MS[iv]))
    a0=int(np.searchsorted(md.ts,start)); bpy=365*DAY/INTERVAL_MS[iv]; c=Costs.for_venue(vn)
    for p in expand_grid(s.param_grid,400):
        r=simulate(md,s.weights(md,p),c).returns[a0:]
        sd=r.std(ddof=1); sr=(r.mean()/sd) if sd>0 else 0.0     # per-period SR
        byiv.setdefault(iv,[]).append(sr)
    n+=1
print("cells enumerated",n,"results in summary",len(d['results']))
EG=0.5772156649015329
var={iv:float(np.var(v,ddof=1)) for iv,v in byiv.items()}
N=run['n_trials']
for iv in ("1h","4h","6h","1d"):
    sr0=math.sqrt(var[iv])*((1-EG)*norm.ppf(1-1/N)+EG*norm.ppf(1-1/(N*math.e)))
    bpy=365*DAY/INTERVAL_MS[iv]
    print(f"{iv}: n_sr {len(byiv[iv])} my var {var[iv]:.8f} lab var {run['trial_sharpe_variance_by_interval'][iv]:.8f} | SR0 per-period {sr0:.5f} annualised {sr0*math.sqrt(bpy):.3f}")
pooled=float(np.var([x for v in byiv.values() for x in v],ddof=1)); print("POOLED(run-1 bug) var",pooled)
for iv in ("1h","4h","6h","1d"):
    sr0=math.sqrt(pooled)*((1-EG)*norm.ppf(1-1/N)+EG*norm.ppf(1-1/(N*math.e))); print("pooled SR0 annualised",iv,round(sr0*math.sqrt(365*DAY/INTERVAL_MS[iv]),2))

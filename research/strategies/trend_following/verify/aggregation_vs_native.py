import time, numpy as np, httpx
from research.lab.venues import get_venue, aggregate_candles, INTERVAL_MS, KuCoin, DYDX, Hyperliquid, Bitfinex
DAY=86400000
end=(int(time.time()*1000)//DAY)*DAY
start=end-330*DAY
def cmp(name, ad, iv, native_iv_map):
    bar=INTERVAL_MS[iv]
    one=ad.candles("BTC","1h",start,end) if False else None
    for a in ["BTC","ETH","SOL"]:
        if a not in ad.assets: continue
        h=ad.candles(a,"1h",(start//bar)*bar,(end//bar)*bar)
        agg=aggregate_candles(h,bar)
        # native: temporarily call fetch with native interval
        ad.intervals=dict(ad.intervals); ad.intervals[iv]=native_iv_map
        nat=ad.candles(a,iv,(start//bar)*bar,(end//bar)*bar)
        del ad.intervals[iv]
        nd={x["ts"]:x for x in nat}; ag={x["ts"]:x for x in agg}
        common=sorted(set(nd)&set(ag))
        dev={}
        for f in ("open","high","low","close"):
            d=np.array([abs(ag[t][f]/nd[t][f]-1) for t in common]); dev[f]=d.max()*1e4, np.percentile(d,99)*1e4
        vd=np.array([abs(ag[t]["volume"]/nd[t]["volume"]-1) if nd[t]["volume"] else 0 for t in common])
        print(f"{name:11s}{a:4s}{iv} native {len(nat)} agg {len(agg)} shared {len(common)} only_native {len(set(nd)-set(ag))} only_agg {len(set(ag)-set(nd))} | max dev bps o/h/l/c "+
              "/".join(f"{dev[f][0]:.2f}" for f in dev)+" | p99 "+"/".join(f"{dev[f][1]:.2f}" for f in dev)+f" | max vol dev {vd.max()*100:.2f}%")
ku=KuCoin(); ku_native_6h="6hour"
cmp("kucoin",ku,"4h","4hour"); cmp("kucoin",ku,"6h","6hour")
cmp("bitfinex",Bitfinex(),"6h","6h")
cmp("dydx",DYDX(),"4h","4HOURS")
cmp("hyperliquid",Hyperliquid(),"4h","4h")

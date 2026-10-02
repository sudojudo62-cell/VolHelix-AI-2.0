import time, numpy as np
from research.lab.venues import get_venue
from research.lab.data import build_market_data
from research.lab.engine import simulate, Costs
from research.strategies.trend_following import ATCoreLong
DAY=86400000; end=(int(time.time()*1000)//DAY)*DAY; start=end-330*DAY
ad=get_venue("dydx")
for iv in ("1h","4h"):
    md=build_market_data(ad,["SOL"],iv,start,end,with_funding=True,allow_head_gap_bars=int(90*DAY/{"1h":3.6e6,"4h":1.44e7}[iv]))
    p={"L_days":5,"theta":0.02,"alpha":2.0}
    W=ATCoreLong().weights(md,p); c=Costs.for_venue("dydx")
    with_f=simulate(md,W,c).returns
    md0=build_market_data(ad,["SOL"],iv,start,end,with_funding=False,allow_head_gap_bars=int(90*DAY/{"1h":3.6e6,"4h":1.44e7}[iv]))
    no_f=simulate(md0,W,c).returns
    diff=(no_f-with_f).sum()
    # independent: raw events from the API, position at the event = weight decided at the previous bar close (W[i-1]) of bar containing event
    ev=ad.funding("SOL",start,end)
    bar={"1h":3600000,"4h":14400000}[iv]; tot=0.0; nev=0; held=0
    for e in ev:
        i=np.searchsorted(md.ts,e["ts"],side="right")-1
        if i>=1 and e["ts"]<md.ts[i]+bar:
            tot+=W[i-1,0]*e["rate"]; nev+=1; held+= W[i-1,0]>0
    print(f"{iv}: events {len(ev)} in-bars {nev} held {held} | summed raw funding while long {tot:.6f} | engine diff {diff:.6f} | total event sum {sum(e['rate'] for e in ev):.6f} | "
          f"ret no-funding {np.prod(1+no_f)-1:.4%} with {np.prod(1+with_f)-1:.4%} | md.funding sum {md.funding.sum():.6f} nonzero bars {(md.funding!=0).sum()}")
    print("  first events", ev[:2], "last", ev[-1])

# funding is wired for every perp venue: md.funding.sum() must equal the sum of the adapter's raw events inside the bars
for vn, a, iv in (("hyperliquid", "BTC", "4h"), ("deribit", "BTC", "1h"), ("dydx", "BTC", "1d")):
    ad = get_venue(vn)
    m = build_market_data(ad, [a], iv, start, end, with_funding=True, allow_head_gap_bars=int(90 * DAY / {"1h": 3.6e6, "4h": 1.44e7, "1d": 8.64e7}[iv]))
    ev = [e for e in ad.funding(a, start, end) if m.ts[0] <= e["ts"] < m.ts[-1] + {"1h": 3600000, "4h": 14400000, "1d": 86400000}[iv]]
    print(f"{vn} {a} {iv}: events {len(ev)} raw sum {sum(e['rate'] for e in ev):.6f} md.funding.sum {m.funding.sum():.6f}")

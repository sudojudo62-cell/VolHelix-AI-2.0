import time, numpy as np
from research.lab.venues import get_venue
from research.lab.data import validate_candles
DAY=86400000
end=(int(time.time()*1000)//DAY)*DAY
start=end-(60+180+90)*DAY
bf, ku = get_venue("bitfinex"), get_venue("kucoin")
for a in ["BTC","ETH","SOL","AVAX","LINK"]:
    for iv in ["1h","1d","4h","6h"]:
        try:
            r=bf.candles(a,iv,start,end)
        except Exception as e:
            print(a,iv,"ERR",e); continue
        q=validate_candles(r,iv,start,end)
        try:
            k=ku.candles(a,iv,start,end) if iv!="6h" else ku.candles(a,iv,start,end)
        except Exception as e:
            print(a,iv,"kucoin ERR",str(e)[:100]); k=[]
        kd={x["ts"]:x for x in k}
        common=[x for x in r if x["ts"] in kd]
        ratio=np.array([x["close"]/kd[x["ts"]]["close"] for x in common]) if common else np.array([np.nan])
        print(a,iv,"n",q["n"],"exp",q["expected"],"miss%",q["missing_pct"],"maxgap",q["max_gap_bars"],"bad",q["bad_ohlc"],"head",q.get("head_gap_bars"),"tail",q.get("tail_gap_bars"),
              "| shared",len(common),"close ratio med %.4f min %.4f max %.4f"%(np.median(ratio),ratio.min(),ratio.max()))

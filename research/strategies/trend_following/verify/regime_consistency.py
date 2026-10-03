import time, numpy as np
from research.lab.venues import get_venue
from research.lab.regime import regime_labels
DAY=86400000; end=(int(time.time()*1000)//DAY)*DAY; start=end-330*DAY
for vn in ("kucoin","dydx"):
    ad=get_venue(vn); L={}
    for iv in ("1h","4h","1d"):
        r=ad.candles("BTC",iv,start,end); ts=np.array([x["ts"] for x in r]); c=np.array([x["close"] for x in r])
        L[iv]=dict(zip(ts.tolist(),regime_labels(ts,c)))
    # compare on instants present in 1h/4h/1d: every day-open instant (00:00 UTC) exists in all three
    days=[t for t in L["1d"] if t in L["1h"] and t in L["4h"]]
    mism_4=sum(L["1d"][t]!=L["4h"][t] for t in days); mism_1=sum(L["1d"][t]!=L["1h"][t] for t in days)
    # also every 4h instant vs the 1h label at the same instant
    c4=[t for t in L["4h"] if t in L["1h"]]; m41=sum(L["4h"][t]!=L["1h"][t] for t in c4)
    from collections import Counter
    print(vn,"00:00 instants",len(days),"1d!=4h",mism_4,"1d!=1h",mism_1,"| 4h instants",len(c4),"4h!=1h",m41, Counter(L["1d"].values()).most_common(3))

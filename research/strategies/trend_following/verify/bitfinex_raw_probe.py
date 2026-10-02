import httpx, time, datetime as dt
H=3600000
end=(int(time.time()*1000)//86400000)*86400000
def get(u,**p):
    r=httpx.get(u,params=p,timeout=25); print(r.status_code, u.split('/v2')[-1], p); time.sleep(1.2); return r
# raw shape
r=get("https://api-pub.bitfinex.com/v2/candles/trade:1h:tBTCUSD/hist",limit=3,sort=1,start=end-10*H,end=end)
print(r.json())
# symbol existence
for s in ["tBTCUSD","tETHUSD","tSOLUSD","tAVAX:USD","tLINK:USD","tAVAXUSD","tLINKUSD"]:
    r=get(f"https://api-pub.bitfinex.com/v2/candles/trade:1D:{s}/hist",limit=2,sort=-1)
    print(s, r.json()[:2] if r.status_code==200 else r.text[:100])
# pagination limit: ask limit=10000 for 1h across 330d
start=end-330*86400000
r=get("https://api-pub.bitfinex.com/v2/candles/trade:1h:tBTCUSD/hist",start=start,end=end,limit=10000,sort=1)
d=r.json(); print("rows",len(d), "first",d[0][0],"last",d[-1][0],"expected",330*24)
print(dt.datetime.utcfromtimestamp(d[0][0]/1000), dt.datetime.utcfromtimestamp(d[-1][0]/1000))

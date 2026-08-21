import requests, hmac, hashlib, time

key = 'GMKqo9T6NQrXLU5oNhZt9YOSS29FqNdI8Z56S9vXjDy1rRNAqlBIMjiggg1TCXqF'
secret = 'h2QJe2zXkostu8CC2YAIMAgFzN9a53wYGtgWajttKrrSctGhF06FOBFbpxAA7g9Q'

ts = int(time.time() * 1000)
params = {'timestamp': ts, 'recvWindow': 10000}
query = '&'.join(f'{k}={v}' for k, v in params.items())
sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

r = requests.get(
    'https://testnet.binance.vision/api/v3/account',
    params={**params, 'signature': sig},
    headers={'X-MBX-APIKEY': key},
    timeout=10
)

print(f"Status: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print("BINANCE TESTNET CONNECTED!")
    print("\nBalances with funds:")
    for b in data.get('balances', []):
        free = float(b['free'])
        locked = float(b['locked'])
        if free > 0 or locked > 0:
            asset = b['asset'].encode('ascii', 'ignore').decode()
            print(f"  {asset}: {free} (locked: {locked})")
else:
    print(f"Error: {r.text[:300]}")

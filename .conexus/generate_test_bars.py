import numpy as np
import pandas as pd
from pathlib import Path

np.random.seed(42)
out = Path('data/market/bars.parquet')
out.parent.mkdir(parents=True, exist_ok=True)

# about 18 months of business days, covering default windows through 2026-07-10
dates = pd.bdate_range('2025-01-02', '2026-07-10')
symbols = [f'TEST{i:03d}' for i in range(1, 16)]
rows = []
for idx, symbol in enumerate(symbols):
    base = 20 + idx * 3.5
    drift = 0.00015 + idx * 0.000035
    vol = 0.012 + (idx % 5) * 0.002
    ret = np.random.normal(drift, vol, len(dates))
    # create differentiated momentum tiers for strategy selection
    ret = ret + np.linspace(-0.00005, 0.00025 + idx * 0.000015, len(dates))
    close = base * np.cumprod(1 + ret)
    open_ = close * (1 + np.random.normal(0, 0.003, len(dates)))
    high = np.maximum(open_, close) * (1 + np.abs(np.random.normal(0.004, 0.002, len(dates))))
    low = np.minimum(open_, close) * (1 - np.abs(np.random.normal(0.004, 0.002, len(dates))))
    volume = np.random.randint(800_000, 5_000_000, len(dates)) * (1 + idx/30)
    for d, o, h, l, c, v in zip(dates, open_, high, low, close, volume):
        rows.append({
            'date': d.strftime('%Y-%m-%d'),
            'symbol': symbol,
            'open': round(float(o), 4),
            'high': round(float(h), 4),
            'low': round(float(l), 4),
            'close': round(float(c), 4),
            'volume': int(v),
        })

df = pd.DataFrame(rows)
df.to_parquet(out, index=False)
print('WROTE', out.resolve())
print('rows', len(df), 'symbols', df['symbol'].nunique(), 'start', df['date'].min(), 'end', df['date'].max())
print('HEAD')
print(df.head(5).to_string(index=False))
print('TAIL')
print(df.tail(5).to_string(index=False))
import sqlite3, json
from pathlib import Path
p=Path('data/app/alphalab.db')
con=sqlite3.connect(p)
con.row_factory=sqlite3.Row
cur=con.cursor()
print('TABLE_COUNTS')
for t in ['strategies','signals','signal_targets','backtests','backtest_returns','backtest_weights','orders','journal']:
    print(t, cur.execute(f'SELECT COUNT(*) c FROM {t}').fetchone()['c'])
print('BACKTESTS')
for r in cur.execute('SELECT id,strategy_id,start_date,end_date,total_return,annual_return,annual_vol,sharpe,max_drawdown,n_periods,tags,notes FROM backtests ORDER BY run_at DESC LIMIT 3'):
    print(json.dumps(dict(r), ensure_ascii=False))
print('SIGNAL_TARGETS')
for r in cur.execute('SELECT signal_id,symbol,target_weight,current_weight FROM signal_targets ORDER BY symbol LIMIT 10'):
    print(json.dumps(dict(r), ensure_ascii=False))
print('RETURNS')
for r in cur.execute('SELECT backtest_id,date,strategy,benchmark FROM backtest_returns ORDER BY date LIMIT 8'):
    print(json.dumps(dict(r), ensure_ascii=False))
con.close()
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    yaml_path TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backtests (
    id TEXT PRIMARY KEY,
    strategy_id TEXT REFERENCES strategies(id),
    config_yaml TEXT NOT NULL,
    code_version TEXT,
    start_date TEXT,
    end_date TEXT,
    run_at TEXT DEFAULT (datetime('now')),
    total_return REAL,
    annual_return REAL,
    annual_vol REAL,
    sharpe REAL,
    max_drawdown REAL,
    n_periods INTEGER,
    tags TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_backtests_strategy ON backtests(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtests_run_at ON backtests(run_at DESC);

CREATE TABLE IF NOT EXISTS backtest_returns (
    backtest_id TEXT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    strategy REAL NOT NULL,
    benchmark REAL,
    PRIMARY KEY (backtest_id, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS backtest_weights (
    backtest_id TEXT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (backtest_id, date, symbol)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    strategy_id TEXT REFERENCES strategies(id),
    profile TEXT NOT NULL DEFAULT 'demo',
    signal_date TEXT NOT NULL,
    generated_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_signals_strategy_latest
    ON signals(strategy_id, signal_date DESC, generated_at DESC);

CREATE TABLE IF NOT EXISTS signal_targets (
    signal_id TEXT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    target_weight REAL NOT NULL,
    current_weight REAL,
    PRIMARY KEY (signal_id, symbol)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    account_id TEXT DEFAULT 'paper',
    signal_id TEXT REFERENCES signals(id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    fill_price REAL,
    fill_quantity REAL,
    commission REAL,
    status TEXT DEFAULT 'pending',
    broker_order_id TEXT,
    broker TEXT DEFAULT 'paper',
    submitted_at TEXT DEFAULT (datetime('now')),
    filled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_signal ON orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);

CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_positions (
    account_id TEXT NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    market_price REAL,
    market_value REAL,
    unrealized_pnl REAL,
    price_date TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, symbol)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS paper_fills (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    order_id TEXT NOT NULL REFERENCES orders(id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    filled_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_paper_fills_account
    ON paper_fills(account_id, filled_at DESC);

CREATE TABLE IF NOT EXISTS paper_nav (
    account_id TEXT NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    equity REAL NOT NULL,
    PRIMARY KEY (account_id, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_runs_created
    ON research_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS research_run_steps (
    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, name)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS journal (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    strategy_id TEXT,
    title TEXT,
    content TEXT NOT NULL,
    tags TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_date ON journal(date DESC);

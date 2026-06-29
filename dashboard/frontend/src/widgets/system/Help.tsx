export function HelpWidget() {
  return (
    <div className="panel">
      <h2>Help</h2>
      <div className="help-grid">
        <section>
          <h3>Core Loop</h3>
          <code>DataEngine -&gt; StrategyConfig -&gt; SignalEngine -&gt; run_backtest</code>
        </section>
        <section>
          <h3>Data</h3>
          <code>market/bars.parquet</code>
          <code>fundamentals/fundamentals.parquet</code>
          <code>factors/factor_returns.parquet</code>
        </section>
        <section>
          <h3>Adapters</h3>
          <code>MarketDataProvider</code>
          <code>FundamentalProvider</code>
          <code>FactorProvider</code>
        </section>
      </div>
    </div>
  )
}


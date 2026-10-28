"""Demo: portfolio + performance + walk-forward forecast on DEMO data only.

Run: python examples/demo_portfolio.py
No network, no API key, no real company. Demo prices are synthetic.
"""
from toolkit import data, forecast, performance, portfolio

prices_a = data.demo_series()
prices_b = [p * 0.98 + 2 for p in prices_a]
prices_c = [p * 1.01 - 1 for p in prices_a]
rets = [portfolio.returns_from_prices(p) for p in (prices_a, prices_b, prices_c)]

print("Equal weights:", portfolio.equal_weights(3))
print("Min-variance:", [round(w, 4) for w in portfolio.min_variance_weights(rets)])
print("Max-Sharpe (unconstrained):", [round(w, 4) for w in portfolio.max_sharpe_weights(rets)])
print("Inverse-vol:", [round(w, 4) for w in portfolio.inverse_volatility_weights(rets)])

port_rets = [sum(w * r for w, r in zip(portfolio.equal_weights(3), col)) for col in zip(*rets)]
print("Portfolio performance (DEMO):", performance.performance_report(port_rets, prices_a, label="demo-equal-weight"))

results = forecast.walk_forward_evaluate(
    prices_a,
    {"naive": forecast.naive_forecast,
     "moving_average": lambda t: forecast.moving_average_forecast(t, 3),
     "trend": forecast.linear_trend_forecast},
    train_size=10,
)
for name, r in results.items():
    print(f"Forecast {name}: MAE={r['mae']:.2f} RMSE={r['rmse']:.2f} "
          f"coverage={r['interval_coverage']:.2f} beats_naive={r.get('beats_naive_mae')}")

print("Free data sources:", [s["source"] for s in data.list_free_sources()])

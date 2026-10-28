"""Worked example on DEMO data only — not a real company, not advice.

Run: python examples/demo_company.py (from the repo root, after pip install -e .)
"""

from toolkit import ratios, valuation

r = ratios.profitability(revenue=1000.0, gross_profit=400.0, operating_profit=150.0, net_income=90.0)
print("Profitability (demo):", r)
print("DuPont:", ratios.dupont_roe(90.0, 1000.0, 2000.0, 800.0))
print("Liquidity:", ratios.liquidity(500.0, 250.0, inventory=100.0))
print("DCF value (demo):", round(valuation.dcf_value([100.0, 110.0, 121.0], 0.10, 0.03, 130.0), 2))
print("Gordon value (demo):", round(valuation.gordon_value(5.0, 0.10, 0.03), 2))

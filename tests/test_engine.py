"""引擎的數字驗證。執行:python tests/test_engine.py(在 retirement_simulator/ 目錄)"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from engine.labor_pension import calculate_labor_pension
from engine.simulation import run_monte_carlo
from engine.schema import button_diff, merge_diff, EXAMPLE_BASELINE_PARAMS as B, validate_baseline
from engine.data_loader import load_returns_csv, DataError

n = 0
def check(name, cond):
    global n
    print(("PASS " if cond else "FAIL ") + name); n += (not cond)

# --- 勞保公式(手算:45000×30×0.00775+3000=13462.5;45000×30×0.0155=20925,取 B 式)---
r = calculate_labor_pension(45000, 30, 65)
check("勞保 65 歲 = 20,925、B 式", r.monthly_pension == 20925 and r.base_formula == "B")
check("勞保 60 歲提前 5 年 -20%", calculate_labor_pension(45000, 30, 60).monthly_pension == round(20925 * 0.8))
check("勞保 70 歲延後 5 年 +20%", calculate_labor_pension(45000, 30, 70).monthly_pension == round(20925 * 1.2))
check("年資很短時 A 式較高", calculate_labor_pension(30000, 5, 65).base_formula == "A")
try: calculate_labor_pension(45000, 30, 59); check("59 歲應報錯", False)
except ValueError: check("59 歲報錯", True)

# --- 模擬 ---
st, dep = np.random.default_rng(0).normal(0.08, 0.2, 40), np.full(40, 0.015)
def sim(**kw):
    a = dict(initial_private_assets=8e6, stock_pct=.6, deposit_pct=.4, annual_labor_pension=250_000,
             annual_spending=480_000, simulation_years=30, historical_stock_returns=st,
             historical_deposit_returns=dep, n_simulations=500, seed=7)
    a.update(kw); return run_monte_carlo(**a)
check("相同種子 -> 結果完全相同", sim().median_terminal_wealth == sim().median_terminal_wealth)
check("花費 0、定存 100% -> 終值 = 8e6×1.015^30 且不破產",
      abs(sim(annual_spending=0, stock_pct=0, deposit_pct=1).median_terminal_wealth - 8e6 * 1.015**30) < 1
      and sim(annual_spending=0).bankruptcy_probability == 0)
check("花費極高 -> 破產機率 100%", sim(annual_spending=5e7, annual_labor_pension=0).bankruptcy_probability == 1.0)
check("花費越高,破產機率不會下降", sim(annual_spending=900_000).bankruptcy_probability >= sim().bankruptcy_probability)
check("路徑不為負、起點正確", (sim().paths >= 0).all() and (sim().paths[:, 0] == 8e6).all())
try: sim(stock_pct=.5, deposit_pct=.4); check("比例相加不為 1 應報錯", False)
except ValueError: check("比例相加不為 1 報錯", True)

# --- 追問按鈕 ---
check("延後兩年 65->67", button_diff(B, "delay2") == {"retirement_age": 67})
at70 = merge_diff(B, {"retirement_age": 70}); check("70 歲再延後不超過 70", button_diff(at70, "delay2")["retirement_age"] == 70)
at60 = merge_diff(B, {"retirement_age": 60}); check("60 歲再提前不低於 60", button_diff(at60, "advance2")["retirement_age"] == 60)
up = merge_diff(B, button_diff(B, "stock_up")); check("股票 +20pp 後 0.8/0.2 且通過驗證", up["private_assets"]["stock_pct"] == 0.8 and not validate_baseline(up))
at100 = merge_diff(B, {"private_assets": {"stock_pct": 0.9, "deposit_pct": 0.1}})
check("股票 90% 再加碼封頂 100%", not validate_baseline(merge_diff(at100, button_diff(at100, "stock_up"))))
check("花費減 10%", button_diff(B, "spend_down") == {"monthly_spending": 36000})

# --- CSV 驗證 ---
def csv(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8"); f.write(text); f.close(); return f.name
rows = "\n".join(f"{1990+i},{0.05+0.01*(i%5):.3f}" for i in range(25))
d = load_returns_csv(csv("year,return\n" + rows)); check("正常 CSV", len(d.returns) == 25 and d.first_year == 1990 and not d.warnings)
check("接受 BOM 與大寫欄名", len(load_returns_csv(csv("﻿Year,Return\n" + rows)).returns) == 25)
for name, text in {
    "缺欄位": "year,ret\n" + rows,
    "百分比寫成 12.3": "year,return\n" + "\n".join(f"{1990+i},12.3" for i in range(25)),
    "含 % 字串": "year,return\n" + "\n".join(f"{1990+i},5%" for i in range(25)),
    "年份重複": "year,return\n" + "\n".join("1990,0.05" for _ in range(25)),
    "太短": "year,return\n1990,0.05\n1991,0.02",
    "含空值": "year,return\n" + rows + "\n2015,",
}.items():
    try: load_returns_csv(csv(text)); check(f"CSV {name} 應報錯", False)
    except DataError: check(f"CSV {name} 報錯", True)
sys.exit(1 if n else 0)

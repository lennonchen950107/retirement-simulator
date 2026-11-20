"""LLM 輸出 JSON 的容錯測試。執行:python tests/test_llm_json.py(在 retirement_simulator/ 目錄)"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from engine.schema import load_baseline_from_llm, run_full_simulation, EXAMPLE_BASELINE_PARAMS as B

good = json.dumps(B, ensure_ascii=False)
cases = {
    "標準 JSON": (good, True),
    "```json 程式碼區塊": ("```json\n" + good + "\n```", True),
    "前後有說明文字": ("好的,結果如下:\n" + good + "\n以上。", True),
    "金額是字串": (good.replace("8000000", '"8,000,000"'), True),
    "年齡 65.0": (good.replace('"retirement_age": 65', '"retirement_age": 65.0'), True),
    "模擬年數 30.0": (good.replace('"simulation_years": 30', '"simulation_years": 30.0'), True),
    "比例寫成 60/40": (good.replace("0.6", "60").replace("0.4", "40"), False),
    "missing 物件": ('{"missing": ["insured_years"]}', False),
    "退休年齡 75": (good.replace('"retirement_age": 65', '"retirement_age": 75'), False),
    "不是 JSON": ("抱歉,我無法處理這個問題。", False),
    "欄位是 null": (good.replace('"monthly_spending": 40000', '"monthly_spending": null'), False),
}
rng = np.random.default_rng(0)
stock, dep = rng.normal(0.08, 0.2, 40), np.full(40, 0.015)
fails = 0
for name, (text, should_pass) in cases.items():
    params, errs = load_baseline_from_llm(text)
    ok = (params is not None) == should_pass
    if params is not None:
        run_full_simulation(params, stock, dep, n_simulations=100, seed=1)  # 不該丟例外
    print(("PASS" if ok else "FAIL"), name, "" if not errs else "->" + errs[0])
    fails += not ok

# --- 三種結果的分辨(Prompt 一缺資料時應該「追問」,不是報錯或硬跑)---
from engine.schema import interpret_llm_output
partial = json.loads(good)
partial["labor_pension"]["insured_years"] = None
partial["monthly_spending"] = None
outcome_cases = [
    ("明確回報 missing -> missing", '{"missing": ["insured_years"]}', "missing"),
    ("missing 用 labor_pension.insured_years 寫法 -> missing", '{"missing": ["labor_pension.insured_years"]}', "missing"),
    ("模型用 null 代替 missing -> 也是 missing", json.dumps(partial), "missing"),
    ("整個欄位不見 -> missing", json.dumps({k: v for k, v in B.items() if k != "monthly_spending"}), "missing"),
    ("missing 與其他欄位並存 -> missing", '{"current_age": 55, "missing": ["monthly_spending"]}', "missing"),
    ("退休 75 歲 -> invalid", good.replace('"retirement_age": 65', '"retirement_age": 75'), "invalid"),
    ("不是 JSON -> invalid", "我不確定", "invalid"),
    ("正常 -> ok", good, "ok"),
]
for name, text, expect in outcome_cases:
    o = interpret_llm_output(text)
    hit = o.kind == expect
    print(("PASS" if hit else "FAIL"), name, "->", o.followup if o.kind == "missing" else "")
    fails += not hit
sys.exit(1 if fails else 0)

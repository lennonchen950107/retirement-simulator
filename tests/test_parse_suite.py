"""驗證 parse_suite 的評分邏輯本身是對的(完美答案全過、各種常見錯誤都抓得到)。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_suite as ps

n = 0
def check(name, cond):
    global n
    print(("PASS " if cond else "FAIL ") + name); n += (not cond)

def params(d):
    g = lambda k: d.get(k)
    return {"current_age": g("current_age"), "retirement_age": g("retirement_age"),
            "labor_pension": {"insured_years": g("insured_years"), "avg_insured_salary": g("avg_insured_salary"), "claim_type": "normal"},
            "private_assets": {"total_amount": g("total_amount"), "stock_pct": g("stock_pct"), "deposit_pct": g("deposit_pct")},
            "monthly_spending": g("monthly_spending"), "simulation_years": 30}

def perfect(cid):
    e = ps.EXPECT[cid]
    if e["kinds"] == {"ok"} or e["kinds"] == {"invalid"}:
        return json.dumps(params(e["present"]))
    if e.get("need_unsupported"):
        return json.dumps({**params(e["present"]), "unsupported": ["房地產"]})
    out = params(e.get("present", {}))
    names = e.get("exact_missing") or e.get("must_missing") or set()
    miss = sorted("stock_pct" if x == "allocation" else x for x in names)
    if "allocation" in names: miss.append("deposit_pct")
    out["missing"] = miss
    return json.dumps(out)

# 完美答案(每句產生「正確的模型輸出」)全部通過
res = {cid: perfect(cid) for cid in ps.EXPECT}
bad = [cid for cid, r in res.items() if not ps.grade_one(cid, r)[0]]
check("16 句完美答案全部通過", not bad)
if bad: print("   失敗:", bad, [ps.grade_one(c, res[c])[1] for c in bad])

def fails(cid, raw): return not ps.grade_one(cid, raw)[0]
# 常見錯誤都要被抓到
a3 = json.loads(res["A3"]); a3["labor_pension"]["insured_years"] = 34
check("A3 把工作年資 34 當成勞保年資 -> 失敗", fails("A3", json.dumps(a3)))
a4 = json.loads(res["A4"]); a4["private_assets"].update(stock_pct=.8, deposit_pct=.2)
check("A4 股票與定存顛倒 -> 失敗", fails("A4", json.dumps(a4)))
b2 = json.loads(res["B2"]); b2["labor_pension"]["insured_years"] = 30; b2["missing"].remove("insured_years")
check("B2 缺資料時亂猜勞保年資 -> 失敗", fails("B2", json.dumps(b2)))
b2 = json.loads(res["B2"]); b2["missing"] = ["insured_years"]
check("B2 漏列缺少欄位 -> 失敗", fails("B2", json.dumps(b2)))
check("B2 用中文欄位名稱也能正確辨識", not fails("B2", json.dumps({**json.loads(res["B2"]), "missing": ["勞保年資", "平均月投保薪資", "股票比例", "定存比例"]})))
b4 = json.loads(res["B4"]); b4["current_age"] = 55
check("B4 把『五十幾歲』當成 55 -> 失敗", fails("B4", json.dumps(b4)))
d1 = json.loads(res["D1"]); d1["retirement_age"] = 65
check("D1 擅自把 50 歲退休改成 65 -> 失敗", fails("D1", json.dumps(d1)))
d2 = json.loads(res["D2"]); d2["private_assets"].update(stock_pct=.75, deposit_pct=.25)
check("D2 擅自正規化比例 -> 失敗(而且會被當成 ok)", fails("D2", json.dumps(d2)))
d3 = json.loads(res["D3"]); d3["private_assets"].update(stock_pct=4/7, deposit_pct=3/7)
check("D3 把房地產吸收進股票與定存 -> 失敗", fails("D3", json.dumps(d3)))
c1 = json.loads(res["C1"]); c1["current_age"] = 55
check("C1 背景敘述中亂猜年齡 -> 失敗", fails("C1", json.dumps(c1)))
d3 = json.loads(res["D3"]); d3.pop("unsupported")
check("D3 沒標示 unsupported -> 失敗", fails("D3", json.dumps(d3)))
from engine.schema import interpret_llm_output
o = interpret_llm_output(res["D3"])
check("unsupported -> 程式回報『不支援房地產』", o.kind == "invalid" and "房地產" in o.errors[0])
o = interpret_llm_output(json.dumps({**params(ps.EXPECT["A1"]["present"]), "unsupported": ["黃金"]}))
check("其餘欄位都正確,只要有 unsupported 仍不可模擬", o.kind == "invalid")
check("輸出不是 JSON -> 失敗", fails("A1", "抱歉,我無法處理"))
check("用 ```json 包起來仍可評分", not fails("A1", "```json\n" + res["A1"] + "\n```"))
nulls = params(ps.EXPECT["C3"]["present"])   # 模型用 null 代替 missing 的 C3
check("C3 用 null 代替 missing 也算正確", not fails("C3", json.dumps(nulls)))
check("解析 test_inputs.md 找到 16 句", len(ps.load_cases()) == 16)
sys.exit(1 if n else 0)

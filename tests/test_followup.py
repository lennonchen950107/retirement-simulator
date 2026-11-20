"""自由追問的解析、套用與驗證測試(不呼叫 LLM)。執行:python tests/test_followup.py(在 retirement_simulator/ 目錄)"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.schema import EXAMPLE_BASELINE_PARAMS as B
from engine.followup import interpret_followup

n = 0
def check(name, cond):
    global n
    if not cond:
        n += 1
        print("失敗:", name)

def ch(*changes, intent="change"):
    return json.dumps({"intent": intent, "changes": [dict(field=f, op=o, value=v) for f, o, v in changes]}, ensure_ascii=False)

def run(raw):
    return interpret_followup(raw, B)

# --- 正常:由程式算出新值 ---
o = run(ch(("retirement_age", "add", 2)))
check("晚兩年 -> 67", o.kind == "change" and o.params["retirement_age"] == 67)
check("說明文字含新舊值", "65 → 67" in o.summary)
o = run(ch(("retirement_age", "add", -3)))
check("提早三年 -> 62", o.kind == "change" and o.params["retirement_age"] == 62)
o = run(ch(("monthly_spending", "multiply", 0.9)))
check("花費 x0.9 -> 36000", o.kind == "change" and o.params["monthly_spending"] == 36000)
o = run(ch(("monthly_spending", "add", -5000)))
check("花費 -5000 -> 35000", o.params["monthly_spending"] == 35000)
o = run(ch(("total_amount", "add", 2000000)))
check("資產 +200 萬", o.params["private_assets"]["total_amount"] == 10_000_000)
o = run(ch(("total_amount", "multiply", 0.5)))
check("資產減半", o.params["private_assets"]["total_amount"] == 4_000_000)
o = run(ch(("stock_pct", "set", 0.4)))
check("股票 40% -> 定存 60%", o.params["private_assets"]["stock_pct"] == 0.4 and o.params["private_assets"]["deposit_pct"] == 0.6)
o = run(ch(("deposit_pct", "set", 1)))
check("定存全部 -> 股票 0", o.params["private_assets"]["stock_pct"] == 0 and o.params["private_assets"]["deposit_pct"] == 1)
o = run(ch(("stock_pct", "add", 0.2)))
check("股票 +20 個百分點", o.params["private_assets"]["stock_pct"] == 0.8 and o.params["private_assets"]["deposit_pct"] == 0.2)
o = run(ch(("insured_years", "set", 35)))
check("勞保年資 35", o.params["labor_pension"]["insured_years"] == 35)
o = run(ch(("avg_insured_salary", "set", 50000)))
check("投保薪資 50000", o.params["labor_pension"]["avg_insured_salary"] == 50000)
o = run(ch(("simulation_years", "set", 20)))
check("模擬 20 年", o.params["simulation_years"] == 20)
o = run(ch(("retirement_age", "set", 68), ("monthly_spending", "set", 50000)))
check("一次改兩項", o.kind == "change" and o.params["retirement_age"] == 68 and o.params["monthly_spending"] == 50000)
o = run("```json\n" + ch(("retirement_age", "add", 1)) + "\n```")
check("程式碼區塊也能解析", o.kind == "change" and o.params["retirement_age"] == 66)
o = run(ch(("勞保年資", "set", 32)))
check("中文欄位名稱可辨認", o.kind == "change" and o.params["labor_pension"]["insured_years"] == 32)
check("不會修改傳入的 baseline", B["retirement_age"] == 65 and B["monthly_spending"] == 40000)
check("intent 缺漏但有 changes 仍視為 change", run('{"changes":[{"field":"retirement_age","op":"add","value":1}]}').kind == "change")

# --- 不改參數:看不懂、超出範圍 ---
check("unclear", run('{"intent":"unclear","changes":[]}').kind == "unclear")
check("out_of_scope", run('{"intent":"out_of_scope","changes":[]}').kind == "out_of_scope")
check("out_of_scope 有訊息", "可以調整" in run('{"intent":"out_of_scope"}').message)
check("沒有 changes -> unclear", run('{"intent":"change","changes":[]}').kind == "unclear")
check("未知欄位 -> unclear", run(ch(("inflation", "set", 0.02))).kind == "unclear")
check("不可調整的欄位(目前年齡) -> unclear", run(ch(("current_age", "set", 58))).kind == "unclear")
check("未知 op -> unclear", run(ch(("retirement_age", "increase", 2))).kind == "unclear")
check("value 是字串 -> unclear", run('{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":"2"}]}').kind == "unclear")
check("value 是布林 -> unclear", run('{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":true}]}').kind == "unclear")
check("changes 不是 list -> unclear", run('{"intent":"change","changes":{"field":"retirement_age"}}').kind == "unclear")
check("比例寫成 40 而不是 0.4 -> unclear", run(ch(("stock_pct", "set", 40))).kind == "unclear")
check("multiply 0 -> unclear", run(ch(("monthly_spending", "multiply", 0))).kind == "unclear")
check("超過 3 項 -> unclear", run(ch(*[("retirement_age", "add", 1)] * 4)).kind == "unclear")
check("同欄位兩次 -> unclear", run(ch(("retirement_age", "add", 1), ("retirement_age", "add", 1))).kind == "unclear")
check("股票與定存同時改 -> unclear", run(ch(("stock_pct", "set", 0.5), ("deposit_pct", "set", 0.5))).kind == "unclear")

# --- invalid:格式錯誤,或調整後數值不合理(不套用) ---
check("不是 JSON -> invalid", run("抱歉我不懂").kind == "invalid")
o = run(ch(("retirement_age", "add", 8)))
check("晚 8 年(73 歲) -> invalid 且說明範圍", o.kind == "invalid" and "60 到 70" in " ".join(o.errors) and o.params is None)
o = run(ch(("stock_pct", "add", 0.6)))
check("股票 120% -> invalid", o.kind == "invalid" and "0% 到 100%" in " ".join(o.errors))
o = run(ch(("retirement_age", "multiply", 1.01)))
check("退休年齡出現小數 -> invalid", o.kind == "invalid" and "整數" in " ".join(o.errors))
o = run(ch(("monthly_spending", "add", -40000)))
check("花費變 0 -> invalid", o.kind == "invalid" and "大於 0" in " ".join(o.errors))
o = run(ch(("retirement_age", "set", 65)))
check("沒有改變 -> invalid 並說明", o.kind == "invalid" and "沒有改變" in " ".join(o.errors))
check("user_messages 對 invalid 回傳 errors", run(ch(("retirement_age", "add", 8))).user_messages == run(ch(("retirement_age", "add", 8))).errors)
check("user_messages 對 unclear 回傳 message", len(run('{"intent":"unclear"}').user_messages) == 1)

sys.exit(1 if n else 0)

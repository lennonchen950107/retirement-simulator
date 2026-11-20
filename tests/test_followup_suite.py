"""驗證追問評分器本身(不呼叫 LLM)。執行:python tests/test_followup_suite.py(在 retirement_simulator/ 目錄)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import followup_suite as fs

n = 0
def check(name, cond):
    global n
    if not cond:
        n += 1
        print("失敗:", name)

ref = {c[2]: fs.REFERENCE[c[0]] for c in fs.CASES}
check("每一句都有標準答案", set(fs.REFERENCE) == {c[0] for c in fs.CASES} and len(fs.CASES) == 18)
rows = fs.run_cases(lambda t: ref[t])
check("標準答案模型 -> 全部通過", all(r[3] for r in rows))

# 亂答的模型:每一句都回「退休年齡 +2」。該拒絕的、該改別項的都應該被抓出來
rows = fs.run_cases(lambda t: '{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":2}]}')
passed = {r[0] for r in rows if r[3]}
check("亂答模型只會答對 A1", passed == {"A1"})

# 算錯的模型:把「晚兩年」當成設成 2 -> 抓得出來
check("set 2 歲 -> 被擋下、不算通過", not fs.grade("A1", '{"intent":"change","changes":[{"field":"retirement_age","op":"set","value":2}]}')[0])
# 用不同的合法寫法得到同樣結果 -> 都算對
check("A3 用 set 35000 也算對", fs.grade("A3", '{"intent":"change","changes":[{"field":"monthly_spending","op":"set","value":35000}]}')[0])
check("A5 用 deposit_pct 0.6 也算對", fs.grade("A5", '{"intent":"change","changes":[{"field":"deposit_pct","op":"set","value":0.6}]}')[0])
# 多改了不該改的欄位 -> 不算對
check("多改欄位 -> 不通過", not fs.grade("A1", fs.REFERENCE["B1"])[0])
check("該拒絕卻改了參數 -> 不通過", not fs.grade("C2", fs.REFERENCE["A1"])[0])
check("不是 JSON -> 該改的句子不通過", not fs.grade("A1", "好的,我幫你處理")[0])
check("D1 要由程式擋下(invalid),模型說 unclear 就不算通過", not fs.grade("D1", '{"intent":"unclear","changes":[]}')[0])

sys.exit(1 if n else 0)

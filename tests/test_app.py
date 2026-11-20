"""用 Streamlit AppTest 無頭跑真正的 app.py。執行:python tests/test_app.py"""
import os, sys, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
from streamlit.testing.v1 import AppTest
import llm_hooks
from engine.schema import EXAMPLE_BASELINE_PARAMS as B

n = 0
def check(name, cond):
    global n
    print(("PASS " if cond else "FAIL ") + name); n += (not cond)

def new_app():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60); at.run(); return at
def texts(els): return " ".join(e.value for e in els)

# 表單 -> 試算 -> 追問按鈕
at = new_app(); check("啟動無例外", not at.exception)
at.sidebar.button[0].click().run()  # 表單的「開始試算」
check("表單試算成功、有 3 個指標", not at.exception and len(at.metric) == 3)
before = at.metric[0].value
at.button(key="delay2").click().run()
check("延後兩年按鈕可用、標題出現", not at.exception and any("延後兩年退休" in s.value for s in at.subheader))
check("按鈕後有調整前後比較(delta)", at.metric[1].delta is not None)
for k in ["advance2", "stock_up", "spend_down"]:
    at.button(key=k).click().run(); check(f"按鈕 {k} 無例外", not at.exception)
check("沒有 LLM 時解讀區有模板文字與說明", "免責" in texts(at.info) or "不代表未來績效" in texts(at.info))

# 一句話描述:假 LLM
class Fake:
    def __init__(self, r): self.r = r
    def complete(self, s, u, **k):
        if isinstance(self.r, Exception): raise self.r
        return self.r
def free_text(reply, text="我 55 歲勞保 30 年"):
    llm_hooks.llm_client = Fake(reply)
    a = new_app(); a.sidebar.radio[0].set_value("一句話描述").run()
    a.sidebar.text_area[0].set_value(text); a.sidebar.button[0].click().run(); return a   # 表單內:輸入與送出同一次提交
a = free_text(json.dumps(B)); check("自由文字 -> 成功試算", not a.exception and len(a.metric) == 3)
a = free_text('{"missing": ["insured_years", "monthly_spending"]}')
check("缺欄位 -> 用中文追問", "勞保保險年資" in texts(a.error) and "退休後每月花費" in texts(a.error) and len(a.metric) == 0)
a = free_text("我不懂"); check("非 JSON -> 白話錯誤、不當掉", not a.exception and len(a.error) >= 1)
a = free_text(TimeoutError("逾時")); check("API 失敗 -> 退回表單提示", not a.exception and "表單" in texts(a.error))
bad = json.loads(json.dumps(B)); bad["retirement_age"] = 75
a = free_text(json.dumps(bad)); check("退休年齡 75 -> 驗證錯誤顯示", "60 到 70" in texts(a.error))
strs = json.loads(json.dumps(B)); strs["private_assets"]["total_amount"] = "8,000,000"
a = free_text(json.dumps(strs)); check("金額字串被自動修正後成功", not a.exception and len(a.metric) == 3)

# 資料不足 -> 追問 -> 補充 -> 成功(第二次的輸入會和第一次累積後一起送給 LLM)
seen = []
class Seq:
    def __init__(self): self.i = 0
    def complete(self, s, u, **k):
        seen.append(u); self.i += 1
        return '{"missing": ["monthly_spending"]}' if self.i == 1 else json.dumps(B)
llm_hooks.llm_client = Seq()
a = new_app(); a.sidebar.radio[0].set_value("一句話描述").run()
a.sidebar.text_area[0].set_value("我 55 歲勞保 30 年"); a.sidebar.button[0].click().run()
check("第一輪:資料不足 -> 追問、沒有跑模擬", "每月花費" in texts(a.error) and len(a.metric) == 0)
a.sidebar.text_area[0].set_value("每月花 4 萬"); a.sidebar.button[0].click().run()
check("第二輪:補充後成功試算", not a.exception and len(a.metric) == 3)
check("第二輪送給 LLM 的內容包含第一輪說過的話", len(seen) >= 2 and "我 55 歲勞保 30 年" in seen[1] and "每月花 4 萬" in seen[1])
# 內容不合法(退休年齡 75)被擋下後,下一句新描述不能和舊的錯誤描述接在一起
seen2 = []
class BadThenOk:
    def __init__(self): self.i = 0
    def complete(self, s, u, **k):
        seen2.append(u); self.i += 1
        return json.dumps(bad) if self.i == 1 else json.dumps(B)
llm_hooks.llm_client = BadThenOk()
a = new_app(); a.sidebar.radio[0].set_value("一句話描述").run()
a.sidebar.text_area[0].set_value("舊的錯誤描述"); a.sidebar.button[0].click().run()
check("不合法 -> 顯示驗證錯誤與重述提示", "60 到 70" in texts(a.error) and "重新完整描述" in texts(a.error))
a.sidebar.text_area[0].set_value("全新的描述"); a.sidebar.button[0].click().run()
check("不合法之後的新描述不會夾帶舊描述", len(seen2) >= 2 and "舊的錯誤描述" not in seen2[1] and "全新的描述" in seen2[1])
check("不合法之後的新描述可成功試算", not a.exception and len(a.metric) == 3)

# 自由追問:一句話 -> LLM 只判斷怎麼改 -> 程式算新值 -> 重跑。試算後才會出現追問輸入框
class FU:
    def __init__(self, reply): self.reply, self.seen = reply, []
    def complete(self, s, u, **k):
        self.seen.append((s, u))
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply
FUKEY = "FormSubmitter:followup_form-送出追問"
def ask(reply, text="如果晚兩年退休呢?"):
    fu = FU(reply); llm_hooks.llm_client = fu
    a = new_app(); a.sidebar.button[0].click().run()
    a.text_input(key="followup_text").set_value(text); a.button(key=FUKEY).click().run()
    return a, fu
a, fu = ask('{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":2}]}')
check("自由追問 -> 成功重算", not a.exception and any("退休年齡 65 → 67" in s.value for s in a.subheader) and bool(a.metric[1].delta))
check("自由追問 -> 追問呼叫只送出使用者那一句話,沒附目前參數", "如果晚兩年退休呢?" in [u for _, u in fu.seen])
a, _ = ask('{"intent":"unclear","changes":[]}', "再保守一點")
check("自由追問看不懂 -> 提示、不重算", not a.exception and "具體的說法" in texts(a.error) and not a.metric[1].delta)
a, _ = ask('{"intent":"out_of_scope","changes":[]}', "推薦我買哪檔股票")
check("自由追問超出範圍 -> 說明可調整項目", "可以調整" in texts(a.error))
a, _ = ask('{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":8}]}', "晚八年退休")
check("自由追問調整後不合理 -> 白話錯誤、不重算", "60 到 70" in texts(a.error) and not a.metric[1].delta)
a, _ = ask(TimeoutError("逾時"))
check("自由追問 API 失敗 -> 提示改用按鈕、不當掉", not a.exception and "按鈕" in texts(a.error))
a, _ = ask("{}", "   ")
check("自由追問空白 -> 白話提示", not a.exception and "請先輸入" in texts(a.error))
a, _ = ask('{"intent":"change","changes":[{"field":"retirement_age","op":"add","value":2}]}')
a.text_input(key="followup_text").set_value("再晚兩年"); a.button(key=FUKEY).click().run()
check("自由追問可連續使用(以上一次調整後的參數為基準)", not a.exception and any("67 → 69" in s.value for s in a.subheader))

# 追問改變模擬年數(例如「多活五年」):前後長度不同,圖表不能當掉,中位數不比較、白話解說只講調整後
a, fu = ask('{"intent":"change","changes":[{"field":"simulation_years","op":"add","value":5}]}', "如果我多活五年呢")
check("模擬年數改變 -> 不當掉", not a.exception)
check("模擬年數改變 -> 標題顯示 30 → 35 年", any("30 → 35 年" in s.value for s in a.subheader))
check("模擬年數改變 -> 說明不比較中位數", "不直接比較" in " ".join(c.value for c in a.caption))
check("模擬年數改變 -> 中位數沒有前後差異", not a.metric[2].delta)
check("模擬年數改變 -> 白話解說沒有收到調整前資料", not any("【調整前】" in u for _, u in fu.seen))
sys.exit(1 if n else 0)

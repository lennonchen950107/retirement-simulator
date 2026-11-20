"""LLM 接點測試(用假的 LLM,不需要 API 金鑰)。執行:python tests/test_hooks.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import llm_hooks
from engine.guard import numbers_not_in_context
from engine.schema import run_full_simulation, format_context_for_llm, EXAMPLE_BASELINE_PARAMS as B, merge_diff

n = 0
def check(name, cond):
    global n
    print(("PASS " if cond else "FAIL ") + name); n += (not cond)

class Fake:
    def __init__(self, reply=None, err=None): self.reply, self.err = reply, err
    def complete(self, system, user, **kw):
        if self.err: raise self.err
        return self.reply(system, user) if callable(self.reply) else self.reply

st, dep = np.random.default_rng(0).normal(0.08, 0.2, 40), np.full(40, 0.015)
r1 = run_full_simulation(B, st, dep, n_simulations=300, seed=1)
r2 = run_full_simulation(merge_diff(B, {"retirement_age": 67}), st, dep, n_simulations=300, seed=1)
ctx, prev = format_context_for_llm(r2), format_context_for_llm(r1)

# 模板:單一情境與兩情境,數字都必須來自 context
for label, c, p in [("單一情境", ctx, None), ("兩情境比較", ctx, prev)]:
    t = llm_hooks.template_narrate(c, p)
    check(f"模板({label})數字全來自 context", not numbers_not_in_context(t, c + (p or "")) and t.endswith(llm_hooks.DISCLAIMER))

# LLM 不可用 -> 退回模板、不丟例外
llm_hooks.llm_client = None
text, src = llm_hooks.narrate_with_status(ctx); check("沒有 client -> 模板", src == "template" and llm_hooks.DISCLAIMER in text)
llm_hooks.llm_client = Fake(err=TimeoutError("逾時")); check("API 逾時 -> 模板", llm_hooks.narrate_with_status(ctx)[1] == "template")
llm_hooks.llm_client = Fake(reply="  "); check("空白回覆 -> 模板", llm_hooks.narrate_with_status(ctx)[1] == "template")

# LLM 亂編數字 -> 被擋下
llm_hooks.llm_client = Fake(reply="你的資產會多出 150 萬。" + llm_hooks.DISCLAIMER)
check("LLM 編造數字被擋下", llm_hooks.narrate_with_status(ctx)[1] == "template")
# LLM 守規矩 -> 採用,缺免責聲明時程式補上
good = "模擬中資產被領光的比例是 0.0%。"
llm_hooks.llm_client = Fake(reply=good); t, s = llm_hooks.narrate_with_status(ctx)
check("守規矩的 LLM 輸出被採用並補免責聲明", s == "llm" and t.endswith(llm_hooks.DISCLAIMER))
# 模型自己附了免責聲明(全形標點)時,不可以再重複附一次
full = "模擬中資產被領光的比例是 0.0%。" + llm_hooks.DISCLAIMER
llm_hooks.llm_client = Fake(reply=full); t, s = llm_hooks.narrate_with_status(ctx)
check("模型已附全形標點免責聲明 -> 只出現一次", s == "llm" and t.count("本結果為") == 1)
llm_hooks.llm_client = Fake(reply=full.replace("，", ",")); t, s = llm_hooks.narrate_with_status(ctx)
check("模型附半形標點免責聲明 -> 也只出現一次", s == "llm" and t.count("本結果為") == 1)
# 兩情境時 prompt 同時帶有調整前與調整後
seen = {}
llm_hooks.llm_client = Fake(reply=lambda sy, us: seen.setdefault("u", us) and good)
llm_hooks.narrate_with_status(ctx, prev); check("兩情境 prompt 含【調整前】【調整後】", "【調整前】" in seen["u"] and "【調整後】" in seen["u"])

# 自由文字解析
llm_hooks.llm_client = Fake(reply="```json\n" + json.dumps(B, ensure_ascii=False) + "\n```")
check("解析:程式碼區塊包著的 JSON", llm_hooks.parse_user_description("我 55 歲……")["retirement_age"] == 65)
llm_hooks.llm_client = Fake(reply='{"missing": ["insured_years"]}')
check("解析:missing", llm_hooks.parse_user_description("我 55 歲") == {"missing": ["insured_years"]})
llm_hooks.llm_client = Fake(reply="抱歉我不懂")
try: llm_hooks.parse_user_description("今天天氣"); check("解析:非 JSON 應丟 ValueError", False)
except ValueError: check("解析:非 JSON 丟 ValueError", True)
llm_hooks.llm_client = Fake(err=ConnectionError("斷線"))
try: llm_hooks.parse_user_description("我 55 歲"); check("解析:API 失敗應丟 LLMUnavailable", False)
except llm_hooks.LLMUnavailable: check("解析:API 失敗丟 LLMUnavailable", True)
for bad in ["", "x" * 501]:
    try: llm_hooks.parse_user_description(bad); check("解析:空白/過長應報錯", False)
    except ValueError: check(f"解析:輸入長度 {len(bad)} 報錯", True)
check("explain prompt 內含的免責聲明與程式的一字不差、且分別規範單組與前後比較兩種情況", llm_hooks.DISCLAIMER in llm_hooks.load_prompt("explain") and "【調整前】" in llm_hooks.load_prompt("explain") and "情況二" in llm_hooks.load_prompt("explain"))
check("載入最新版 prompt", "參數擷取器" in llm_hooks.load_prompt("parse") and "解說" in llm_hooks.load_prompt("explain") and "追問解析器" in llm_hooks.load_prompt("followup"))
sys.exit(1 if n else 0)

"""llm_client 的行為測試(不連網、不需要金鑰:用假的 Gemini 呼叫取代)。"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_client as c

n = 0
def check(name, cond):
    global n
    print(("PASS " if cond else "FAIL ") + name); n += (not cond)

log = os.path.join(tempfile.mkdtemp(), "usage.jsonl")
os.environ["LLM_USAGE_LOG"] = log
os.environ.pop("LLM_API_KEY", None)

try: c.complete("s", "u"); check("沒有金鑰應報錯", False)
except RuntimeError as e: check("沒有金鑰 -> 明確錯誤、不重試", "LLM_API_KEY" in str(e))

os.environ["LLM_API_KEY"] = "fake-key-for-test"
calls = {"n": 0}
def ok(system, user, t): calls["n"] += 1; return "你好", {"prompt_tokens": 120, "output_tokens": 30, "total_tokens": 150}
c._call_gemini = ok
check("成功時回傳文字", c.complete("你是一個參數擷取器", "hi") == "你好" and calls["n"] == 1)

def flaky(system, user, t):
    calls["n"] += 1
    if calls["n"] == 2: raise TimeoutError("逾時")
    return "OK", {}
calls["n"] = 1; c._call_gemini = flaky; c.time.sleep = lambda s: None
check("暫時性錯誤重試一次後成功", c.complete("s", "u") == "OK")

def dead(system, user, t): raise ConnectionError("斷線")
c._call_gemini = dead
try: c.complete("s", "u"); check("持續失敗應丟例外", False)
except RuntimeError as e: check("持續失敗 -> 丟例外(llm_hooks 會接住)", "斷線" in str(e))

rows = [json.loads(l) for l in open(log, encoding="utf-8")]
check("使用紀錄:有 parse 的 token 與秒數", any(r["label"] == "parse" and r["prompt_tokens"] == 120 and "seconds" in r for r in rows))
check("使用紀錄:追問呼叫標成 followup", c._infer_label("你是一個追問解析器。") == "followup" and c._infer_label("你是一個參數擷取器") == "parse")
check("使用紀錄:失敗也有記錄", any(not r["ok"] for r in rows))
check("使用紀錄:不含對話內容", all("hi" not in json.dumps(r) and "u" != r.get("user") for r in rows) and not any("user" in r or "content" in r for r in rows))

# 與 llm_hooks 串起來:壞掉的 client 不能讓介面當機
import llm_hooks
llm_hooks.llm_client = c
check("API 全掛 -> narrate 退回模板", llm_hooks.narrate_with_status("- 破產機率:1.0%\n- 30年後中位數餘額:100")[1] == "template")
class OldStyle: FALLBACK = {}   # 只交了 safe_json_call、沒有 complete 的 llm_client
llm_hooks.llm_client = OldStyle
try: llm_hooks.parse_description("我 55 歲"); check("介面不符應報錯", False)
except llm_hooks.LLMUnavailable as e: check("llm_client 沒有 complete -> 明確訊息", "complete" in str(e))

check("safe_json_call:合法 JSON", c.safe_json_call(lambda: '{"a": 1}') == {"a": 1})
check("safe_json_call:壞掉 -> FALLBACK", c.safe_json_call(lambda: "不是 json") == c.FALLBACK)
sys.exit(1 if n else 0)

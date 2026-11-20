"""確認 Gemini 金鑰與 llm_client 能通。執行(在 retirement_simulator/ 目錄):python tests/llm_ping.py
前提:pip install google-genai;環境變數 LLM_API_KEY 已設定(Windows:setx LLM_API_KEY "你的金鑰",重開 VS Code)。
金鑰只放環境變數,不要寫進程式碼。測試只用範例句子,不要輸入真實個人財務資料。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_USAGE_LOG", "llm_usage.jsonl")
import llm_client

try:
    print("模型回應:", llm_client.complete("你是測試助手。", "請只回覆:OK"))
except Exception as e:
    sys.exit(f"呼叫失敗:{e}\n常見原因:沒設 LLM_API_KEY、沒裝 google-genai、模型名稱已更換(404,用 LLM_MODEL 指定)、超過每分鐘上限(429,等一分鐘)")
last = [json.loads(l) for l in open(os.environ["LLM_USAGE_LOG"], encoding="utf-8")][-1]
print("用量:", {k: last.get(k) for k in ("prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens", "seconds")})
print("成功。成本要把 thinking_tokens 一起算進輸出 token。")

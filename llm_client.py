"""
LLM 呼叫層(角色二負責維護)。

介面(llm_hooks.py 只會用到 complete):
    complete(system, user, *, temperature=0.0) -> str      回傳模型純文字;失敗時丟例外(llm_hooks 會接住)
    safe_json_call(call_fn, retries=1) -> dict             角色二任務書原本的規格,保留相容

設定(只從環境變數讀,金鑰絕對不寫進程式碼或貼到群組):
    LLM_API_KEY     必填。Google AI Studio 金鑰
    LLM_MODEL       選填,預設 gemini-3.5-flash-lite(404 時用 client.models.list() 查帳號可用的模型,以這個變數覆蓋)
    LLM_TIMEOUT_S   選填,預設 30 秒
    LLM_USAGE_LOG   選填,預設 llm_usage.jsonl;每次呼叫記一行「呼叫別、token 數、秒數」,
                    不記錄任何對話內容(避免個人財務資料落檔)。角色四算 Cost & Latency 用這個。

注意:Gemini 的呼叫寫法依 google-genai 官方用法與任務書的 llm_ping.py 撰寫,
尚未用真實金鑰實測。角色二請先跑 tests/llm_ping.py 確認,再跑 python tests/test_llm_client.py。
"""

from __future__ import annotations

import json
import os
import time

DEFAULT_MODEL = "gemini-3.5-flash-lite"   # 實測可用(帳號清單內);gemini-2.5-flash 已停用、gemini-3.8-flash 實測持續 503/504
FALLBACK = {"intent": "out_of_scope"}


def _get_setting(name: str, default: str = "") -> str:
    """先看環境變數;沒有的話再看 Streamlit Secrets(部署到 Streamlit Cloud 時設定值放在那裡)。"""
    v = os.environ.get(name)
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except Exception:   # 沒有 secrets 檔、沒裝 streamlit 等情況都當作沒設定
        return default


def _settings() -> tuple[str, str, float]:
    key = _get_setting("LLM_API_KEY").strip()
    if not key:
        raise RuntimeError("沒有設定 LLM_API_KEY(請放在環境變數,或 Streamlit Cloud 的 Secrets;不要寫進程式碼)")
    return key, _get_setting("LLM_MODEL", DEFAULT_MODEL), float(_get_setting("LLM_TIMEOUT_S", "30"))


def _call_gemini(system: str, user: str, temperature: float) -> tuple[str, dict]:
    """實際呼叫 Gemini。回傳 (文字, token 用量)。測試時會被取代。"""
    from google import genai
    from google.genai import types

    key, model, timeout_s = _settings()
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system, temperature=temperature),
    )
    meta = getattr(resp, "usage_metadata", None)
    usage = {
        "prompt_tokens": getattr(meta, "prompt_token_count", None),
        "output_tokens": getattr(meta, "candidates_token_count", None),
        "thinking_tokens": getattr(meta, "thoughts_token_count", None),   # 2.5 Flash 的思考 token,依輸出計費
        "total_tokens": getattr(meta, "total_token_count", None),
    }
    return resp.text or "", usage


def _infer_label(system: str) -> str:
    """從 system prompt 判斷這是哪一種呼叫,讓使用紀錄能分開統計。"""
    if "追問解析器" in system:
        return "followup"
    if "參數擷取器" in system:
        return "parse"
    if "解說" in system:
        return "explain"
    return "other"


def _log_usage(label: str, started: float, usage: dict, ok: bool, error: str = "") -> None:
    """只記次數、token、秒數,不記內容。記錄失敗不影響主流程。"""
    try:
        rec = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "label": label,
            "model": _get_setting("LLM_MODEL", DEFAULT_MODEL),
            "seconds": round(time.time() - started, 2),
            "ok": ok,
            **usage,
        }
        if error:
            rec["error"] = error[:200]
        with open(os.environ.get("LLM_USAGE_LOG", "llm_usage.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _is_transient(e: Exception) -> bool:
    """503/504/429/逾時/連線錯誤才值得重試;401/403/404/400(金鑰、模型名、請求錯)重試沒用。"""
    if "PerDay" in str(e) or "per day" in str(e):   # 每日額度用完:重試只是白等,直接失敗
        return False
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if isinstance(code, int):
        return code in (408, 429, 500, 502, 503, 504)
    msg = f"{type(e).__name__}: {e}"
    return any(k in msg for k in ("503", "504", "429", "UNAVAILABLE", "DEADLINE", "Timeout", "timed out", "Connection"))


def complete(system: str, user: str, *, temperature: float = 0.0, retries: int = 3) -> str:
    """呼叫模型並回傳文字。暫時性錯誤退避重試(2、4、8 秒);沒設金鑰或永久性錯誤時立刻失敗。"""
    _settings()   # 沒金鑰就直接丟例外
    label = _infer_label(system)
    last: Exception | None = None
    for attempt in range(retries + 1):
        started = time.time()
        try:
            text, usage = _call_gemini(system, user, temperature)
            _log_usage(label, started, usage, ok=True)
            return text
        except Exception as e:
            last = e
            _log_usage(label, started, {}, ok=False, error=f"{type(e).__name__}: {e}")
            if attempt < retries and _is_transient(e):
                time.sleep(2.0 * (2 ** attempt))
            else:
                break
    raise RuntimeError(f"LLM 呼叫失敗:{type(last).__name__}: {last}") from last


def safe_json_call(call_fn, retries: int = 1):
    """角色二任務書的原始規格:call_fn() 回傳模型文字。例外或不是合法 JSON 時,重試後回傳 FALLBACK。"""
    for _ in range(retries + 1):
        try:
            return json.loads(call_fn())
        except Exception:
            continue
    return FALLBACK

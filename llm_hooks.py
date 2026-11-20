"""
LLM 接點。app.py 只透過這個檔案碰 LLM,所以 API 與 prompt 換版時,app.py 與 engine/ 都不用改。

鐵則:這裡只做「文字敘事」與「參數解析」,不做任何數字計算。
- narrate():LLM 的輸出會被 engine.guard 檢查,只要出現 context 沒有的數字,
  就丟掉 LLM 的輸出,改用程式模板(數字原封不動取自 context)。LLM 壞掉、逾時時也一樣退回模板。
- parse_user_description():LLM 輸出必須是合法 JSON,並且之後仍要過 validate_baseline()。

依賴:
- llm_client.complete(system, user) -> str   (角色二交付;沒有時介面自動退回表單與模板)
- prompts/<name>_v<N>.txt                    (角色三交付;自動使用版本號最大的那份)
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Any

from engine.followup import FollowupOutcome, interpret_followup
from engine.guard import numbers_not_in_context
from engine.schema import ParseOutcome, coerce_baseline, interpret_llm_output, parse_llm_json

try:
    import llm_client
except Exception:  # 沒有檔案或匯入失敗都視為 LLM 不可用
    llm_client = None

log = logging.getLogger(__name__)

PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
DISCLAIMER = "本結果為依歷史報酬率重抽樣的模擬，不代表未來績效，也不構成投資建議。"
MAX_INPUT_CHARS = 500
MAX_FOLLOWUP_CHARS = 200


LAST_REASON = ""   # 最近一次白話解讀退回模板的原因(沒退回時為空字串),給介面顯示用


def _mask(text: str) -> str:
    """錯誤訊息顯示前遮掉任何長得像金鑰的字串,避免意外洩漏。"""
    text = re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "***", text)
    key = os.environ.get("LLM_API_KEY", "").strip()
    return text.replace(key, "***") if key else text


class LLMUnavailable(RuntimeError):
    """LLM 無法使用(沒設定、逾時、額度、網路等)。app 會顯示白話訊息並退回表單。"""


def load_prompt(name: str) -> str:
    """讀 prompts/<name>_v<N>.txt 中版本號最大的一份。"""
    best, best_v = None, -1
    for path in glob.glob(os.path.join(PROMPT_DIR, f"{name}_v*.txt")):
        m = re.search(r"_v(\d+)\.txt$", path)
        if m and int(m.group(1)) > best_v:
            best, best_v = path, int(m.group(1))
    if best is None:
        raise LLMUnavailable(f"找不到 prompt 檔案:prompts/{name}_v*.txt")
    with open(best, encoding="utf-8") as f:
        return f.read().strip()


def _complete(system: str, user: str) -> str:
    if llm_client is None:
        raise LLMUnavailable("找不到 llm_client.py")
    if not callable(getattr(llm_client, "complete", None)):
        raise LLMUnavailable("llm_client.py 需要提供 complete(system, user) 函式(見 INTEGRATION.md)")
    try:
        out = llm_client.complete(system, user)
    except Exception as e:
        raise LLMUnavailable(str(e)) from e
    if not isinstance(out, str) or not out.strip():
        raise LLMUnavailable("LLM 回傳空白內容")
    return out


# ---------------------------------------------------------------------------
# 白話解讀
# ---------------------------------------------------------------------------

def template_narrate(context: str, previous_context: str | None = None) -> str:
    """
    不靠 LLM 的白話解讀:句型固定,數字全部原封不動取自 context。
    LLM 不可用或被數字檢查擋下時使用,保證介面永遠有一段正確的文字。
    """
    def grab(pattern: str, ctx: str) -> str | None:
        m = re.search(pattern, ctx)
        return m.group(1).strip() if m else None

    def facts(ctx: str) -> dict[str, str | None]:
        return {
            "years": grab(r"- (\d+)年後中位數餘額", ctx),
            "ruin": grab(r"- 破產機率:(\S+)", ctx),
            "median": grab(r"- \d+年後中位數餘額:([\d,]+)", ctx),
            "mean": grab(r"- \d+年後平均餘額:([\d,]+)", ctx),
            "age": grab(r"- 退休年齡:(\d+)", ctx),
            "spend": grab(r"- 每月花費:([\d,]+)", ctx),
            "assets": grab(r"- 私人資產:([\d,]+)", ctx),
        }

    now = facts(context)
    if not now["ruin"] or not now["years"]:
        return DISCLAIMER

    parts = [
        f"依目前設定(退休年齡 {now['age']}、私人資產 {now['assets']} 元、每月花費 {now['spend']} 元),"
        f"模擬的 {now['years']} 年中,資產被領光的比例(破產機率)是 {now['ruin']}。"
    ]
    parts.append(
        f"{now['years']} 年後資產的中位數餘額是 {now['median']} 元,平均餘額是 {now['mean']} 元。"
    )
    if previous_context:
        before = facts(previous_context)
        if before["ruin"]:
            parts.append(
                f"和調整前相比:破產機率從 {before['ruin']} 變成 {now['ruin']},"
                f"中位數餘額從 {before['median']} 元變成 {now['median']} 元。"
            )
    parts.append(DISCLAIMER)
    return "".join(parts)


def narrate_with_status(context: str, previous_context: str | None = None) -> tuple[str, str]:
    """回傳 (文字, 來源),來源是 "llm" 或 "template",讓介面能誠實標示。"""
    global LAST_REASON
    LAST_REASON = ""
    fallback = template_narrate(context, previous_context)
    if previous_context:
        user = f"【調整前】\n{previous_context}\n\n【調整後】\n{context}"
    else:
        user = context
    try:
        out = _complete(load_prompt("explain"), user).strip()
    except LLMUnavailable as e:
        LAST_REASON = _mask(str(e))[:300]
        log.warning("narrate 退回模板:%s", LAST_REASON)
        return fallback, "template"

    bad = numbers_not_in_context(out, context + "\n" + (previous_context or ""))
    if bad:
        log.warning("narrate 輸出含 context 沒有的數字 %s,退回模板", sorted(bad))
        LAST_REASON = "AI 的輸出含有輸入資料以外的數字,已被數字檢查擋下"
        return fallback, "template"
    if DISCLAIMER not in out.replace(",", "，"):   # 標點全形/半形差異不算缺漏,避免重複附上兩次
        out = out + DISCLAIMER   # 免責聲明由程式保證附上,不依賴模型
    return out, "llm"


def narrate(context: str, previous_context: str | None = None) -> str:
    return narrate_with_status(context, previous_context)[0]


# ---------------------------------------------------------------------------
# 自由文字初次輸入
# ---------------------------------------------------------------------------

def parse_description(text: str) -> ParseOutcome:
    """
    自由描述 -> ParseOutcome(kind 為 ok / missing / invalid,見 engine/schema.py)。
    LLM 不可用時丟 LLMUnavailable;輸入空白或過長時丟 ValueError。其餘情況都回傳 ParseOutcome。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("請先輸入一段描述")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"描述太長了,請控制在 {MAX_INPUT_CHARS} 字以內")
    return interpret_llm_output(_complete(load_prompt("parse"), text))


def parse_user_description(text: str) -> dict[str, Any]:
    """
    把使用者的自由描述轉成基準參數 JSON(格式見 engine/schema.py)。

    成功:回傳參數 dict(已修正常見型別問題,但「還沒驗證」,呼叫端要再過 validate_baseline)。
    缺欄位:回傳 {"missing": [...]}。
    LLM 不可用:丟 LLMUnavailable。
    LLM 輸出不是合法 JSON:丟 ValueError(白話訊息)。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("請先輸入一段描述")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"描述太長了,請控制在 {MAX_INPUT_CHARS} 字以內")
    raw = _complete(load_prompt("parse"), text)
    obj = parse_llm_json(raw)
    if "missing" in obj:
        if not isinstance(obj["missing"], list):
            raise ValueError("LLM 回報的 missing 格式不正確")
        return {"missing": obj["missing"]}
    return coerce_baseline(obj)


# ---------------------------------------------------------------------------
# 自由追問(結果出來後,使用者用一句話調整條件)
# ---------------------------------------------------------------------------

def parse_followup(text: str, baseline: dict[str, Any]) -> FollowupOutcome:
    """
    一句追問 -> FollowupOutcome(kind 為 change / unclear / out_of_scope / invalid,見 engine/followup.py)。
    只有使用者那一句話會送給模型(不附目前參數,也不附對話歷史);新數值由程式計算。
    LLM 不可用時丟 LLMUnavailable;輸入空白或過長時丟 ValueError。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("請先輸入一句追問")
    if len(text) > MAX_FOLLOWUP_CHARS:
        raise ValueError(f"追問太長了,請控制在 {MAX_FOLLOWUP_CHARS} 字以內")
    return interpret_followup(_complete(load_prompt("followup"), text), baseline)

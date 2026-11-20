"""
自由追問:把使用者的一句追問(例如「如果晚兩年退休呢?」)轉成對目前參數的修改。

分工(與整個專案的原則一致:LLM 不碰計算):
- LLM 只輸出「要改哪個欄位、怎麼改」,例如 {"field": "retirement_age", "op": "add", "value": 2}。
- 新的數值由這個檔案的程式計算(65 + 2 = 67),再走 validate_baseline() 驗證,
  最後交給引擎重算。LLM 輸出不合規格、超出範圍或看不懂時,一律不改參數、不重算。

LLM 輸出格式:
    {"intent": "change" | "out_of_scope" | "unclear",
     "changes": [{"field": <欄位>, "op": "set" | "add" | "multiply", "value": <數字>}, ...]}
    - set:設成 value;add:目前值加上 value(可為負);multiply:目前值乘以 value
    - 最多 3 項變更;同一個欄位不能出現兩次;股票與定存比例只能說其中一個

interpret_followup() 永遠不丟例外,回傳 FollowupOutcome。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from engine.schema import FIELD_LABELS, canonical_field, merge_diff, parse_llm_json, validate_baseline

ALLOWED_FIELDS = (
    "retirement_age", "monthly_spending", "total_amount", "stock_pct", "deposit_pct",
    "insured_years", "avg_insured_salary", "simulation_years",
)
OPS = ("set", "add", "multiply")
MAX_CHANGES = 3

_INT_FIELDS = {"retirement_age", "simulation_years"}
_MONEY_FIELDS = {"monthly_spending", "total_amount", "avg_insured_salary"}
_PCT_FIELDS = {"stock_pct", "deposit_pct"}

MSG_UNCLEAR = ("我不確定你想調整哪個項目。請用具體的說法,"
               "例如「晚兩年退休」「每月花費減少 5000」「股票比例改成 40%」。")
MSG_OUT_OF_SCOPE = ("這個追問目前不在工具能調整的範圍內。"
                    "可以調整:退休年齡、每月花費、私人資產總額、股票/定存比例、"
                    "勞保年資、平均月投保薪資、模擬年數。")
MSG_TOO_MANY = f"一次最多調整 {MAX_CHANGES} 個項目,請分開追問。"
MSG_CONFLICT = "同一個項目被調整了兩次,或同時調整股票與定存比例,請只說其中一個。"
MSG_NO_CHANGE = "這項調整沒有改變任何參數。"


@dataclass
class FollowupOutcome:
    """
    kind == "change"       -> params 是調整後的完整參數(已通過驗證),summary 是白話說明
    kind == "unclear"      -> 看不懂要改什麼,message 請使用者換個說法
    kind == "out_of_scope" -> 超出工具範圍,message 說明可以調整哪些
    kind == "invalid"      -> 格式錯誤或調整後的數值不合理,errors 是白話原因
    """
    kind: str
    params: dict[str, Any] | None = None
    summary: str = ""
    message: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def user_messages(self) -> list[str]:
        """kind 不是 change 時,要顯示給使用者的訊息。"""
        return list(self.errors) if self.kind == "invalid" else ([self.message] if self.message else [])


def _get(baseline: dict, name: str) -> float:
    if name in ("insured_years", "avg_insured_salary"):
        return baseline["labor_pension"][name]
    if name in ("total_amount", "stock_pct", "deposit_pct"):
        return baseline["private_assets"][name]
    return baseline[name]


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _apply(op: str, cur: float, value: float) -> float:
    if op == "set":
        return value
    if op == "add":
        return cur + value
    return cur * value


def _finalize(name: str, x: float) -> float:
    """整理型別:年齡與年數必須是整數;金額四捨五入到元;勞保年資最多兩位小數。"""
    if name in _INT_FIELDS:
        r = round(x)
        if abs(x - r) > 1e-6:
            raise ValueError(f"{FIELD_LABELS[name]}必須是整數")
        return int(r)
    if name in _MONEY_FIELDS:
        return int(round(x))
    r = round(x, 2)
    return int(r) if float(r).is_integer() else r


def _describe(name: str, old: float, new: float) -> str:
    label = FIELD_LABELS[name]
    if name in _PCT_FIELDS:
        return f"{label} {old:.0%} → {new:.0%}"
    if name in _MONEY_FIELDS:
        return f"{label} {old:,.0f} → {new:,.0f} 元"
    unit = "歲" if name == "retirement_age" else "年"
    return f"{label} {old:g} → {new:g} {unit}"


def interpret_followup(raw: str, baseline: dict[str, Any]) -> FollowupOutcome:
    """LLM 原始輸出 -> FollowupOutcome。永遠不丟例外。"""
    try:
        obj = parse_llm_json(raw)
    except ValueError as e:
        return FollowupOutcome("invalid", errors=[str(e)])

    intent = obj.get("intent")
    if intent == "out_of_scope":
        return FollowupOutcome("out_of_scope", message=MSG_OUT_OF_SCOPE)
    raw_changes = obj.get("changes")
    if intent == "unclear" or not isinstance(raw_changes, list) or not raw_changes:
        return FollowupOutcome("unclear", message=MSG_UNCLEAR)
    if len(raw_changes) > MAX_CHANGES:
        return FollowupOutcome("unclear", message=MSG_TOO_MANY)

    entries: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for c in raw_changes:
        if not isinstance(c, dict):
            return FollowupOutcome("unclear", message=MSG_UNCLEAR)
        name = canonical_field(c.get("field", ""))
        op, value = c.get("op"), c.get("value")
        if name not in ALLOWED_FIELDS or op not in OPS or not _num(value):
            return FollowupOutcome("unclear", message=MSG_UNCLEAR)
        if name in _PCT_FIELDS and op != "multiply" and abs(value) > 1:
            return FollowupOutcome("unclear", message=MSG_UNCLEAR)   # 比例必須是 0~1 的小數
        if op == "multiply" and value <= 0:
            return FollowupOutcome("unclear", message=MSG_UNCLEAR)
        key = "stock" if name in _PCT_FIELDS else name
        if key in seen:
            return FollowupOutcome("unclear", message=MSG_CONFLICT)
        seen.add(key)
        entries.append((name, op, float(value)))

    new = copy.deepcopy(baseline)
    parts: list[str] = []
    try:
        for name, op, value in entries:
            cur = _get(baseline, name)
            if name in _PCT_FIELDS:
                x = _apply(op, cur, value)
                stock = x if name == "stock_pct" else 1.0 - x
                if not (-1e-9 <= stock <= 1 + 1e-9):
                    return FollowupOutcome("invalid", errors=["調整後的股票比例會超出 0% 到 100%,請換個說法。"])
                stock = min(max(round(stock, 4), 0.0), 1.0)
                new = merge_diff(new, {"private_assets": {"stock_pct": stock, "deposit_pct": round(1.0 - stock, 4)}})
                parts.append(_describe("stock_pct", baseline["private_assets"]["stock_pct"], stock))
                continue
            val = _finalize(name, _apply(op, cur, value))
            if name in ("insured_years", "avg_insured_salary"):
                new = merge_diff(new, {"labor_pension": {name: val}})
            elif name == "total_amount":
                new = merge_diff(new, {"private_assets": {name: val}})
            else:
                new = merge_diff(new, {name: val})
            parts.append(_describe(name, cur, val))
    except ValueError as e:
        return FollowupOutcome("invalid", errors=[str(e)])

    if new == baseline:
        return FollowupOutcome("invalid", errors=[MSG_NO_CHANGE])
    errors = validate_baseline(new)
    if errors:
        return FollowupOutcome("invalid", errors=errors)
    return FollowupOutcome("change", params=new, summary="、".join(parts))

"""
「LLM 不做計算」的程式化把關:LLM 輸出裡出現的每個數字,都必須能在給它的 context 裡找到。
(對應角色三任務書 Prompt 二的檢查程式;簡報時可展示這是經驗證據。)
"""

import re


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def numbers_not_in_context(output: str, context: str) -> set[str]:
    """回傳 output 裡有、context 裡沒有的數字集合。空集合代表通過。"""
    return numbers_in(output) - numbers_in(context)

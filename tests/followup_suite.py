"""
自由追問的實測評分(用真正的 LLM)。執行(在 retirement_simulator/ 目錄,需設好 LLM_API_KEY):
    python tests/followup_suite.py list     只列出 18 句測試與預期,不呼叫 LLM
    python tests/followup_suite.py live     逐句呼叫 LLM、自動評分(約 1 分半鐘,18 次呼叫)

評分方式:拿 LLM 的輸出走 engine.followup.interpret_followup(跟 app 完全一樣),
對照「基準參數套用預期變動後」的完整參數,所以模型用 set / add / multiply 哪一種都算對,
只看最後算出來的數字。不該改參數的句子(看不懂、超出範圍)只要沒有被改動就算對。

注意:這 18 句是調整 followup prompt 時用的測試集;之後如果改 prompt,
最好另外寫幾句沒看過的句子再測,才不會變成只對這 18 句有效。
"""
from __future__ import annotations

import copy
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.followup import interpret_followup
from engine.schema import EXAMPLE_BASELINE_PARAMS as BASE, merge_diff

# (id, 類別, 句子, 預期)
#   預期 {"change": {...}}:應該 kind == "change",且結果等於「BASE 套用這些扁平欄位」
#   預期 {"kinds": {...}}:kind 必須是其中之一,而且 params 不可被改動
CASES = [
    ("A1", "清楚單項", "如果晚兩年退休呢?", {"change": {"retirement_age": 67}}),
    ("A2", "清楚單項", "我想提早三年退休", {"change": {"retirement_age": 62}}),
    ("A3", "清楚單項", "每個月花費減少 5000", {"change": {"monthly_spending": 35000}}),
    ("A4", "清楚單項", "花費省一成", {"change": {"monthly_spending": 36000}}),
    ("A5", "清楚單項", "股票比例改成 40%", {"change": {"stock_pct": 0.4}}),
    ("A6", "清楚單項", "全部改放定存", {"change": {"stock_pct": 0.0}}),
    ("A7", "清楚單項", "如果我的資產多 200 萬", {"change": {"total_amount": 10_000_000}}),
    ("A8", "清楚單項", "資產減半的話呢", {"change": {"total_amount": 4_000_000}}),
    ("A9", "清楚單項", "股票提高 20 個百分點", {"change": {"stock_pct": 0.8}}),
    ("A10", "清楚單項", "勞保年資如果是 35 年", {"change": {"insured_years": 35}}),
    ("B1", "多項調整", "改成 68 歲退休,每個月花 5 萬", {"change": {"retirement_age": 68, "monthly_spending": 50000}}),
    ("B2", "多項調整", "晚一年退休,花費減一成,股票降到三成",
     {"change": {"retirement_age": 66, "monthly_spending": 36000, "stock_pct": 0.3}}),
    ("C1", "不該改參數", "再保守一點", {"kinds": {"unclear", "out_of_scope"}}),
    ("C2", "不該改參數", "幫我推薦要買哪一檔股票", {"kinds": {"out_of_scope"}}),
    ("C3", "不該改參數", "我想把一部分錢拿去買房地產", {"kinds": {"out_of_scope"}}),
    ("C4", "不該改參數", "如果加入通膨會怎樣?", {"kinds": {"out_of_scope"}}),
    ("D1", "程式把關", "如果晚八年退休", {"kinds": {"invalid"}}),
    ("D2", "程式把關", "股票比例改成 150%", {"kinds": {"unclear", "invalid"}}),
]

# 一個「標準答案」模型會給的輸出,只用來驗證評分器本身(tests/test_followup_suite.py)
def _c(*ch): return '{"intent":"change","changes":[' + ",".join(
    '{"field":"%s","op":"%s","value":%s}' % c for c in ch) + "]}"
REFERENCE = {
    "A1": _c(("retirement_age", "add", 2)), "A2": _c(("retirement_age", "add", -3)),
    "A3": _c(("monthly_spending", "add", -5000)), "A4": _c(("monthly_spending", "multiply", 0.9)),
    "A5": _c(("stock_pct", "set", 0.4)), "A6": _c(("stock_pct", "set", 0)),
    "A7": _c(("total_amount", "add", 2000000)), "A8": _c(("total_amount", "multiply", 0.5)),
    "A9": _c(("stock_pct", "add", 0.2)), "A10": _c(("insured_years", "set", 35)),
    "B1": _c(("retirement_age", "set", 68), ("monthly_spending", "set", 50000)),
    "B2": _c(("retirement_age", "add", 1), ("monthly_spending", "multiply", 0.9), ("stock_pct", "set", 0.3)),
    "C1": '{"intent":"unclear","changes":[]}', "C2": '{"intent":"out_of_scope","changes":[]}',
    "C3": '{"intent":"out_of_scope","changes":[]}', "C4": '{"intent":"out_of_scope","changes":[]}',
    "D1": _c(("retirement_age", "add", 8)), "D2": _c(("stock_pct", "set", 1.5)),
}


def _expected_params(flat: dict) -> dict:
    p = copy.deepcopy(BASE)
    for k, v in flat.items():
        if k in ("insured_years", "avg_insured_salary"):
            p = merge_diff(p, {"labor_pension": {k: v}})
        elif k in ("total_amount", "stock_pct"):
            p = merge_diff(p, {"private_assets": {k: v}})
        else:
            p = merge_diff(p, {k: v})
        if k == "stock_pct":
            p = merge_diff(p, {"private_assets": {"deposit_pct": round(1 - v, 4)}})
    return p


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def grade(case_id: str, raw: str) -> tuple[bool, str]:
    """回傳 (是否通過, 說明)。"""
    expect = next(c[3] for c in CASES if c[0] == case_id)
    o = interpret_followup(raw, BASE)
    if "change" in expect:
        if o.kind != "change":
            return False, f"應該調整參數,結果是 {o.kind}"
        if not _same(o.params, _expected_params(expect["change"])):
            return False, f"算出的參數不對:{o.summary}"
        return True, o.summary
    if o.kind in expect["kinds"]:
        return True, o.kind
    return False, f"結果應為 {'/'.join(sorted(expect['kinds']))},實際是 {o.kind}" + (f"({o.summary})" if o.kind == "change" else "")


def run_cases(complete) -> list[tuple[str, str, str, bool, str]]:
    """complete(sentence) -> 原始輸出字串。回傳 [(id, 類別, 句子, 通過, 說明)]。"""
    rows = []
    for cid, cls, text, _ in CASES:
        try:
            raw = complete(text)
        except Exception as e:
            rows.append((cid, cls, text, False, f"呼叫失敗:{e}")); continue
        ok, why = grade(cid, raw)
        rows.append((cid, cls, text, ok, why))
    return rows


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        for cid, cls, text, exp in CASES:
            print(f"{cid:<4}{cls}  {text}\n      預期:{exp}")
        return 0
    if cmd != "live":
        print(__doc__); return 2
    import llm_hooks
    system = llm_hooks.load_prompt("followup")
    delay = float(os.environ.get("LLM_SUITE_DELAY", "4.5"))   # 免費層有每分鐘請求上限,逐句間隔一下
    raws: dict[str, str] = {}

    def complete(text: str) -> str:
        time.sleep(delay if raws else 0)
        cid = next(c[0] for c in CASES if c[2] == text)
        raws[cid] = llm_hooks._complete(system, text)
        return raws[cid]

    rows = run_cases(complete)
    with open("followup_responses_live.txt", "w", encoding="utf-8") as f:
        for cid, _, text, _, _ in rows:
            f.write(f"### {cid}  {text}\n{raws.get(cid, '(沒有回覆)')}\n\n")
    print("模型原始回覆已存到 followup_responses_live.txt\n")
    for cid, cls, text, ok, why in rows:
        print(f"{cid:<4}{'通過' if ok else '失敗'}  {text}  →  {why}")
    by: dict[str, list[bool]] = {}
    for _, cls, _, ok, _ in rows:
        by.setdefault(cls, []).append(ok)
    print("\n" + "、".join(f"{k} {sum(v)}/{len(v)}" for k, v in by.items()))
    passed = sum(r[3] for r in rows)
    need = math.ceil(0.9 * len(rows))
    print(f"總計 {passed}/{len(rows)}(目標至少 {need}/{len(rows)},約 90%)")
    return 0 if passed >= need else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

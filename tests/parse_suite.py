"""
Prompt 一(參數擷取)的完整測試:角色四的 A~D 類測試句 × 自動評分。

評分不只看「ok / missing / invalid」,還會逐欄比對擷取到的數值,
並檢查:缺資料時有沒有亂猜、不合理的數字有沒有被擅自修改、模糊說法有沒有被當成精確值。

用法(在 retirement_simulator/ 目錄):
    python tests/parse_suite.py sheet                      產生 parse_sheet.md 與 parse_responses.txt
    python tests/parse_suite.py grade parse_responses.txt  評分
    python tests/parse_suite.py live [prompt檔]            直接呼叫 llm_client 自動跑完 16 句並評分(需設好 LLM_API_KEY)

手動測試流程(沒有 API 金鑰):
  1. 執行 sheet。打開 parse_sheet.md,裡面有每一句測試句。
  2. 每一句都開一個「新的對話」:先貼上 prompt 檔案(prompts/parse_v2.txt)全文,換行後貼上測試句。
  3. 把模型的回覆原封不動貼進 parse_responses.txt 對應的 "### A1" 標題下面。
  4. 執行 grade。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.schema import canonical_field, interpret_llm_output, parse_llm_json  # noqa: E402

CASES_MD = os.path.join(ROOT, "tests", "test_inputs.md")
DEFAULT_PROMPT = os.path.join(ROOT, "prompts", "parse_v2.txt")
PASS_RATE = 0.90

ALL8 = ["current_age", "retirement_age", "insured_years", "avg_insured_salary",
        "total_amount", "stock_pct", "deposit_pct", "monthly_spending"]


def full8(age, ret, yrs, sal, amt, stock, dep, spend):
    return dict(zip(ALL8, [age, ret, yrs, sal, amt, stock, dep, spend]))


def group(name: str) -> str:
    """股票與定存比例視為同一組(模型可能只列其中一個,或寫成「資產配置」)。"""
    return "allocation" if name in ("stock_pct", "deposit_pct") else name


# 評分用的預期值是照下列 16 句測試句寫的。test_inputs.md 若被改過,grade 會警告(預期值可能過期)。
SENTENCE_FINGERPRINT = {
    "A1": "e25cc9e83b",
    "A2": "18750dddce",
    "A3": "18447f1d61",
    "A4": "29e2c66418",
    "A5": "1af49545c8",
    "A6": "86021fc35e",
    "B1": "0e28b307e1",
    "B2": "55e0a0967d",
    "B3": "83b9b89517",
    "B4": "62a02e0cd1",
    "C1": "35044bb1be",
    "C2": "23ff1854ec",
    "C3": "ded6b14639",
    "D1": "a5386668ab",
    "D2": "5755d9171a",
    "D3": "b62e596295",
}


# kinds:允許的結果類型;present:應該擷取到的值(若模型有給,必須正確);
# exact_missing:缺漏欄位必須剛好是這些;must_missing:缺漏欄位至少要包含這些;
# forbidden:模糊說法不能被當成這些精確值;note:程式無法自動判斷、需要人看的重點
EXPECT: dict[str, dict] = {
    "A1": dict(kinds={"ok"}, present=full8(55, 65, 30, 45000, 8_000_000, .6, .4, 40000)),
    "A2": dict(kinds={"ok"}, present=full8(58, 63, 32, 42000, 12_000_000, .7, .3, 50000)),
    "A3": dict(kinds={"ok"}, present=full8(56, 65, 29, 44000, 9_000_000, .6, .4, 38000),
               note="陷阱:工作 34 年 ≠ 勞保 29 年"),
    "A4": dict(kinds={"ok"}, present=full8(60, 65, 35, 45800, 10_000_000, .2, .8, 45000),
               note="陷阱:80% 定存、20% 股票,不得顛倒"),
    "A5": dict(kinds={"ok"}, present=full8(57, 67, 28, 40000, 9_000_000, 0, 1, 30000)),
    "A6": dict(kinds={"ok"}, present=full8(54, 64, 29, 43000, 7_500_000, 1, 0, 38000)),
    "B1": dict(kinds={"ok"}, present=full8(58, 65, 32, 42000, 9_500_000, .7, .3, 45000)),
    "B2": dict(kinds={"missing"},
               present=dict(current_age=55, retirement_age=65, total_amount=8_000_000, monthly_spending=35000),
               exact_missing={"insured_years", "avg_insured_salary", "allocation"}),
    "B3": dict(kinds={"missing"},
               present=dict(current_age=59, insured_years=33, avg_insured_salary=42000,
                            total_amount=9_000_000, stock_pct=.75, deposit_pct=.25),
               exact_missing={"retirement_age", "monthly_spending"}),
    "B4": dict(kinds={"missing"},
               must_missing={"retirement_age", "avg_insured_salary", "monthly_spending"},
               forbidden=dict(current_age=[50, 55, 59], insured_years=[30, 29], total_amount=[6_000_000, 6_500_000]),
               note="模糊說法(五十幾歲、快 30 年、六百多萬)不得被當成精確數字"),
    "C1": dict(kinds={"missing"}, exact_missing=set(ALL8) - {"stock_pct", "deposit_pct"} | {"allocation"},
               note="整段都是背景敘述,不得擷取任何個人數據"),
    "C2": dict(kinds={"missing"},
               present=dict(current_age=55, retirement_age=65, avg_insured_salary=45000,
                            total_amount=8_000_000, stock_pct=.6, deposit_pct=.4, monthly_spending=40000),
               exact_missing={"insured_years"}),
    "C3": dict(kinds={"missing"},
               present=dict(current_age=58, insured_years=32, avg_insured_salary=42000,
                            total_amount=10_000_000, stock_pct=.7, deposit_pct=.3),
               exact_missing={"retirement_age", "monthly_spending"}),
    "D1": dict(kinds={"invalid"}, present=full8(45, 50, 25, 42000, 8_000_000, .6, .4, 40000),
               note="退休年齡 50 必須照實保留(不得改成 65)。系統會以「年齡需介於 60~70」擋下"),
    "D2": dict(kinds={"invalid", "missing"}, present=full8(55, 65, 30, 45000, 8_000_000, 1.5, .5, 40000),
               note="150% / 50% 不得被擅自正規化成 75% / 25%"),
    "D3": dict(kinds={"invalid", "missing"},
               present=dict(current_age=57, retirement_age=65, insured_years=32, avg_insured_salary=43000,
                            total_amount=10_000_000, stock_pct=.4, deposit_pct=.3, monthly_spending=45000),
               need_unsupported=True,
               note="房地產 30% 不得併入股票或定存,且要用 unsupported 標示出來(程式據此告知使用者不支援)"),
}


# ---------------------------------------------------------------------------
# 讀測試句
# ---------------------------------------------------------------------------

def stale_cases(cases: dict[str, str]) -> list[str]:
    """回傳「測試句已被改動、或不在評分範圍內」的編號。"""
    import hashlib
    bad = [c for c, t in cases.items() if SENTENCE_FINGERPRINT.get(c) != hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]]
    return sorted(bad)


def load_cases(path: str = CASES_MD) -> dict[str, str]:
    cases: dict[str, str] = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^\|\s*\**([A-D]\d)\**\s*\|(.+?)\|", line)
        if m and m.group(1) in EXPECT:
            cases[m.group(1)] = m.group(2).strip()
    return cases


# ---------------------------------------------------------------------------
# 評分
# ---------------------------------------------------------------------------

def leaves(obj, out=None):
    """把任意巢狀 JSON 攤平成 {葉節點名稱: 值};忽略 missing 與 null。"""
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "missing":
                continue
            if isinstance(v, dict):
                leaves(v, out)
            elif v is not None:
                out[k] = v
    return out


def num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def same(a, b) -> bool:
    x, y = num(a), num(b)
    return x is not None and y is not None and math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9)


def grade_one(case_id: str, raw: str) -> tuple[bool, list[str], str]:
    """回傳 (是否通過, 問題清單, 實際結果類型)。"""
    exp = EXPECT[case_id]
    problems: list[str] = []
    outcome = interpret_llm_output(raw)
    kind = outcome.kind

    if kind not in exp["kinds"]:
        want = " 或 ".join(sorted(exp["kinds"]))
        problems.append(f"結果類型是 {kind},應為 {want}")

    try:
        obj = parse_llm_json(raw)
    except ValueError:
        obj = {}
    got = leaves(obj)

    # 擷取到的值必須正確(模型有給的就要對;沒給則在 missing 情境下視為沒擷取)
    present = exp.get("present", {})
    for field, want in present.items():
        if field in got:
            if not same(got[field], want):
                problems.append(f"{field}:應為 {want},模型給 {got[field]!r}")
        elif kind == "ok" or exp["kinds"] == {"ok"} or exp["kinds"] == {"invalid"}:
            problems.append(f"{field}:沒有擷取到(應為 {want})")

    # 缺資料時:不能亂猜、缺漏清單要正確
    if isinstance(obj.get("missing"), list):
        reported = {group(canonical_field(m) or str(m)) for m in obj["missing"]}
    elif kind == "missing":   # 模型沒用 missing 欄位,而是省略或填 null
        reported = {group(f) for f in ALL8 if f not in got}
    else:
        reported = set()

    if "exact_missing" in exp and kind == "missing":
        want = {group(x) for x in exp["exact_missing"]}
        if reported != want:
            extra, lack = sorted(reported - want), sorted(want - reported)
            if extra:
                problems.append(f"多列了不該缺的欄位:{extra}")
            if lack:
                problems.append(f"漏列了缺少的欄位:{lack}")
        for f in want:                                   # 該缺的欄位不能有猜出來的值
            names = ("stock_pct", "deposit_pct") if f == "allocation" else (f,)
            for n in names:
                if n in got and n not in present:
                    problems.append(f"{n} 應該是缺漏,模型卻猜了 {got[n]!r}")
    if "must_missing" in exp and kind == "missing":
        lack = sorted({group(x) for x in exp["must_missing"]} - reported)
        if lack:
            problems.append(f"漏列了缺少的欄位:{lack}")
    for field, bad_values in exp.get("forbidden", {}).items():
        if field in got and any(same(got[field], b) for b in bad_values):
            problems.append(f"{field}:模糊說法被當成精確值 {got[field]!r}")

    if exp.get("need_unsupported") and not (isinstance(obj.get("unsupported"), list) and obj["unsupported"]):
        problems.append("沒有用 unsupported 標示不支援的資產類別(房地產)")

    if kind == "ok":
        if "simulation_years" in got and not same(got["simulation_years"], 30):
            problems.append(f"simulation_years 應為 30,模型給 {got['simulation_years']!r}")
        if "claim_type" in got and got["claim_type"] != "normal":
            problems.append(f"claim_type 應為 normal,模型給 {got['claim_type']!r}")

    return not problems, problems, kind


def read_responses(path: str) -> dict[str, str]:
    text = open(path, encoding="utf-8-sig").read()          # utf-8-sig:相容帶 BOM 的檔案
    parts = re.split(r"^#{1,4}[ \t]*([A-Da-d]\d)\b[^\n]*$", text, flags=re.M)
    return {parts[i].upper(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def describe_file(path: str, responses: dict[str, str]):
    """評分前先說明讀到了什麼,讓「我明明填了卻顯示未填」一眼找得到原因。"""
    import time
    st = os.stat(path)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    filled = [c for c, r in responses.items() if r.strip()]
    print(f"讀取的檔案:{os.path.abspath(path)}")
    print(f"檔案大小 {st.st_size} 位元組,最後存檔時間 {stamp}")
    print(f"找到 {len(responses)} 個標題,其中有內容的:{', '.join(filled) if filled else '(無)'}\n")
    if st.st_size < 100 and not filled:
        print("⚠ 檔案幾乎是空的。請確認:1) 貼完有按 Ctrl+S 存檔  2) 你編輯的是上面這個路徑的檔案\n")
    elif not responses:
        print("⚠ 找不到任何 ### A1 這樣的標題,請確認沒有改到標題那一行\n")


def grade(responses: dict[str, str]) -> int:
    cases = load_cases()
    stale = stale_cases(cases)
    if stale:
        print(f"⚠ 測試句 {', '.join(stale)} 和評分程式當初對照的版本不同(tests/test_inputs.md 被改過)。")
        print("  這幾句的預期值可能已過期,請把改動告訴 Lennon 更新評分程式。\n")
    done = passed = 0
    by_group: dict[str, list[int]] = {}
    for cid in EXPECT:
        raw = responses.get(cid, "").strip()
        if not raw:
            print(f"{cid}  -- 未填")
            continue
        ok, problems, kind = grade_one(cid, raw)
        done += 1
        passed += ok
        by_group.setdefault(cid[0], [0, 0])
        by_group[cid[0]][1] += 1
        by_group[cid[0]][0] += ok
        print(f"{cid}  {'通過' if ok else '失敗'}  (結果:{kind})")
        for p in problems:
            print(f"      ✗ {p}")
        if EXPECT[cid].get("note"):
            print(f"      ※ 人工確認:{EXPECT[cid]['note']}")
    print("\n分類:" + "、".join(f"{g} 類 {a}/{b}" for g, (a, b) in sorted(by_group.items())))
    if done:
        need = math.ceil(PASS_RATE * len(EXPECT))
        print(f"總計 {passed}/{done}(已填)。任務書門檻:{len(EXPECT)} 句中至少 {need} 句({PASS_RATE:.0%})")
        if done < len(EXPECT):
            print(f"還有 {len(EXPECT) - done} 句沒填。")
    return 0 if done and passed == done else 1


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------

def make_sheet():
    import llm_hooks
    prompt = llm_hooks.load_prompt("parse")          # 自動取版本號最大的 prompt
    cases = load_cases()
    with open("parse_sheet.md", "w", encoding="utf-8") as f:
        f.write("# 測試用完整訊息\n\n"
                "每一句:開一個**新的對話**,把下面灰底方塊的內容**整段複製**(prompt + 測試句已經接好),貼上送出,\n"
                "再把模型的回覆貼進 parse_responses.txt 對應的 ### 編號底下。\n\n")
        for cid in EXPECT:
            f.write(f"## {cid}\n\n````\n{prompt}\n\n{cases.get(cid, '(找不到測試句)')}\n````\n\n")
    target = "parse_responses.txt"
    if os.path.exists(target) and any(read_responses(target).values()):
        target = "parse_responses_new.txt"
        print("⚠ parse_responses.txt 裡已經有你貼的答案,為了不蓋掉它,空白答案檔改存成 parse_responses_new.txt")
    with open(target, "w", encoding="utf-8") as f:
        for cid in EXPECT:
            f.write(f"### {cid}\n\n\n")
    print(f"已產生 parse_sheet.md({len(cases)} 句,每句都附上 prompt)與 {target}(貼回答的地方)")


def live(prompt_path: str | None = None):
    import llm_hooks
    # 沒指定檔案就用目前生效的 prompt(prompts/ 裡版本號最大的 parse_v*.txt)
    system = (open(prompt_path, encoding="utf-8").read().strip() if prompt_path else llm_hooks.load_prompt("parse"))
    cases = load_cases()
    responses = {}
    for cid in EXPECT:
        try:
            responses[cid] = llm_hooks._complete(system, cases[cid])
        except Exception as e:
            print(f"{cid} 呼叫失敗:{e}")
    with open("parse_responses_live.txt", "w", encoding="utf-8") as f:     # 不覆蓋手動貼的 parse_responses.txt
        for cid, r in responses.items():
            f.write(f"### {cid}\n{r}\n\n")
    print("模型原始回覆已存到 parse_responses_live.txt\n")
    return grade(responses)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sheet":
        make_sheet()
    elif cmd == "grade" and len(sys.argv) == 3:
        _r = read_responses(sys.argv[2])
        describe_file(sys.argv[2], _r)
        sys.exit(grade(_r))
    elif cmd == "live":
        sys.exit(live(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        sys.exit(__doc__)

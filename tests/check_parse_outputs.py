"""
角色三用:批次檢查 Prompt 一的輸出。不需要 API 金鑰,不會因為輸出壞掉而當掉。

用法(在 retirement_simulator/ 目錄):
    python tests/check_parse_outputs.py my_outputs.txt

檔案格式:每筆模型原始輸出之間用單獨一行 --- 分隔。
想自動統計通過率,在每筆的第一行寫期望結果(可省略):
    #expect: ok          可直接模擬
    #expect: missing     資料不足,應該追問
    #expect: invalid     格式或數值有問題

範例檔:
    #expect: ok
    {"current_age": 55, ... }
    ---
    #expect: missing
    {"missing": ["insured_years"]}
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from engine.schema import interpret_llm_output, run_full_simulation

st, dep = np.random.default_rng(0).normal(0.08, 0.2, 40), np.full(40, 0.015)

def main(path):
    blocks = [b.strip() for b in re.split(r"^---\s*$", open(path, encoding="utf-8").read(), flags=re.M) if b.strip()]
    counts, graded, passed = {"ok": 0, "missing": 0, "invalid": 0}, 0, 0
    for i, b in enumerate(blocks, 1):
        m = re.match(r"#expect:\s*(ok|missing|invalid)\s*\n", b)
        expect = m.group(1) if m else None
        raw = b[m.end():] if m else b
        o = interpret_llm_output(raw)
        counts[o.kind] += 1
        detail = ""
        if o.kind == "ok":
            r = run_full_simulation(o.params, st, dep, n_simulations=100, seed=1)   # 通過的一定跑得動
            detail = f"可模擬,破產機率 {r['simulation']['bankruptcy_probability']:.1%}"
        elif o.kind == "missing":
            detail = "應追問 -> " + o.followup
        else:
            detail = "錯誤 -> " + ";".join(o.errors)
        mark = ""
        if expect:
            graded += 1; hit = expect == o.kind; passed += hit
            mark = "  [符合期望]" if hit else f"  [不符:期望 {expect}]"
        print(f"#{i} {o.kind:8s}{mark}\n    {detail}")
    print(f"\n共 {len(blocks)} 筆:ok {counts['ok']}、missing {counts['missing']}、invalid {counts['invalid']}")
    if graded:
        print(f"符合期望:{passed}/{graded}({passed/graded:.0%})")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

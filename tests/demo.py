"""
端對端示範腳本:把「勞保估算」+「蒙地卡羅模擬」+「LLM context 格式化」串起來,
讓團隊可以直接執行看到完整流程長怎樣。

執行方式(在 retirement_simulator/ 這層目錄下執行):
    python tests/demo.py

目前使用的歷史報酬率資料是「假資料」(placeholder),
角色二(資料/API)串接真實 TAIEX + 定存利率歷史資料後,
只需要把下面 fake_stock_returns / fake_deposit_returns 換成
從 data/ 讀進來的真實資料即可,其他程式碼完全不用動
——這就是 schema.py 存在的目的:把「資料」跟「計算邏輯」切開。
"""

import sys
import os

# 讓這支腳本不管從哪個目錄執行都能找到 engine 這個 package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from engine.schema import (
    EXAMPLE_BASELINE_PARAMS,
    run_full_simulation,
    format_context_for_llm,
    merge_diff,
)


def main():
    print("=" * 60)
    print("情境一:使用企劃書範例的基準參數,跑一次完整模擬")
    print("=" * 60)

    # TODO(角色二/資料/API):把這兩行換成從 data/ 讀進來的真實
    # TAIEX 年報酬率序列、台灣銀行定存利率序列
    rng_demo = np.random.default_rng(42)
    fake_stock_returns = rng_demo.normal(0.08, 0.20, size=40)
    fake_deposit_returns = np.full(40, 0.015)

    result = run_full_simulation(
        baseline_params=EXAMPLE_BASELINE_PARAMS,
        historical_stock_returns=fake_stock_returns,
        historical_deposit_returns=fake_deposit_returns,
        n_simulations=5000,
        seed=123,
    )

    print(format_context_for_llm(result))

    print("\n" + "=" * 60)
    print("情境二:模擬使用者在追問時說「如果我 67 歲才退休呢?」")
    print("(這裡先手動模擬 LLM 解析出的 diff,之後接上真正的 LLM 解析層即可)")
    print("=" * 60)

    diff_from_llm = {"retirement_age": 67}
    updated_params = merge_diff(EXAMPLE_BASELINE_PARAMS, diff_from_llm)

    result2 = run_full_simulation(
        baseline_params=updated_params,
        historical_stock_returns=fake_stock_returns,
        historical_deposit_returns=fake_deposit_returns,
        n_simulations=5000,
        seed=123,
    )

    print(format_context_for_llm(result2))

    print("\n" + "=" * 60)
    print("兩種情境比較:")
    print("=" * 60)
    print(f"65歲退休 -> 破產機率 {result['simulation']['bankruptcy_probability']:.1%}")
    print(f"67歲退休 -> 破產機率 {result2['simulation']['bankruptcy_probability']:.1%}")


if __name__ == "__main__":
    main()

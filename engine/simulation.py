"""
蒙地卡羅退休提領模擬引擎(確定性計算,不涉及 AI)

核心方法:block bootstrap 重抽樣
- 不是隨機打散歷史報酬率的年份,而是整段連續年份一起抽樣,
  這樣才能保留「連續幾年報酬好壞相關」的真實結構,
  正確呈現順序風險(sequence-of-returns risk)。

每條模擬路徑:
    資產_{t+1} = 資產_t * (1 + 當年報酬率) - (年支出 - 年勞保年金)
報酬率由股票/定存依權重混合,年支出不含勞保已覆蓋的部分。
"""

from dataclasses import dataclass, field
import numpy as np


@dataclass
class SimulationResult:
    bankruptcy_probability: float      # 破產機率(資產於模擬期間內 <= 0 的比例)
    median_terminal_wealth: float      # 終值中位數
    mean_terminal_wealth: float        # 終值平均數
    paths: np.ndarray                  # shape (n_simulations, n_years+1),供 fan chart 使用
    percentile_10: np.ndarray          # 每年的 10th percentile(供繪圖)
    percentile_50: np.ndarray          # 每年的中位數路徑
    percentile_90: np.ndarray          # 每年的 90th percentile


def block_bootstrap_sample(
    historical_returns: np.ndarray,
    n_years: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    從歷史報酬率序列中,用連續區塊重抽樣的方式,
    產生一條長度為 n_years 的模擬報酬率路徑。

    做法:隨機選一個起始點,取連續 n_years 筆歷史資料;
    若歷史資料長度不足,允許從頭銜接(circular block bootstrap)。
    """
    hist_len = len(historical_returns)
    start_idx = rng.integers(0, hist_len)
    indices = [(start_idx + i) % hist_len for i in range(n_years)]
    return historical_returns[indices]


def run_monte_carlo(
    initial_private_assets: float,
    stock_pct: float,
    deposit_pct: float,
    annual_labor_pension: float,
    annual_spending: float,
    simulation_years: int,
    historical_stock_returns: np.ndarray,
    historical_deposit_returns: np.ndarray,
    n_simulations: int = 5000,
    seed: int | None = None,
) -> SimulationResult:
    """
    執行蒙地卡羅退休提領模擬。

    參數:
        initial_private_assets: 私人資產起始總額(元)
        stock_pct: 股票佔比(0~1)
        deposit_pct: 定存佔比(0~1),應與 stock_pct 相加為 1
        annual_labor_pension: 勞保年金提供的年收入(元),用來降低需要從私人資產提領的淨支出
        annual_spending: 退休後每年總支出(元)
        simulation_years: 模擬年數(例如 30)
        historical_stock_returns: 歷史股票年報酬率陣列(例如 [0.12, -0.08, ...])
        historical_deposit_returns: 歷史定存年利率陣列
        n_simulations: 模擬路徑數(預設 5000)
        seed: 隨機種子,方便除錯與重現結果

    回傳:
        SimulationResult,包含破產機率、終值統計與完整路徑(供視覺化)
    """
    if abs(stock_pct + deposit_pct - 1.0) > 1e-6:
        raise ValueError(f"stock_pct + deposit_pct 應等於 1,收到 {stock_pct + deposit_pct}")

    rng = np.random.default_rng(seed)

    # 每年淨支出 = 總支出 - 勞保年金覆蓋的部分(勞保是保底收入,降低提領壓力)
    net_annual_withdrawal = max(0.0, annual_spending - annual_labor_pension)

    paths = np.zeros((n_simulations, simulation_years + 1))
    paths[:, 0] = initial_private_assets

    for i in range(n_simulations):
        stock_path = block_bootstrap_sample(historical_stock_returns, simulation_years, rng)
        deposit_path = block_bootstrap_sample(historical_deposit_returns, simulation_years, rng)
        blended_returns = stock_pct * stock_path + deposit_pct * deposit_path

        wealth = initial_private_assets
        for t in range(simulation_years):
            wealth = wealth * (1 + blended_returns[t]) - net_annual_withdrawal
            wealth = max(wealth, 0.0)  # 資產歸零後不會變負,視為破產狀態持續
            paths[i, t + 1] = wealth

    terminal_wealth = paths[:, -1]
    bankruptcy_probability = float(np.mean(terminal_wealth <= 0))

    return SimulationResult(
        bankruptcy_probability=bankruptcy_probability,
        median_terminal_wealth=float(np.median(terminal_wealth)),
        mean_terminal_wealth=float(np.mean(terminal_wealth)),
        paths=paths,
        percentile_10=np.percentile(paths, 10, axis=0),
        percentile_50=np.percentile(paths, 50, axis=0),
        percentile_90=np.percentile(paths, 90, axis=0),
    )


if __name__ == "__main__":
    # 快速自我測試:用隨便造的示範歷史報酬率資料(非真實資料,只為了驗證邏輯能跑)
    # 真實 TAIEX / 定存資料由角色二(資料/API)串接後取代這裡
    rng_demo = np.random.default_rng(42)
    fake_stock_returns = rng_demo.normal(0.08, 0.20, size=40)   # 模擬 40 年股票報酬,平均8%、標準差20%
    fake_deposit_returns = np.full(40, 0.015)                    # 模擬定存利率固定 1.5%

    result = run_monte_carlo(
        initial_private_assets=8_000_000,
        stock_pct=0.6,
        deposit_pct=0.4,
        annual_labor_pension=32_000 * 12,
        annual_spending=40_000 * 12,
        simulation_years=30,
        historical_stock_returns=fake_stock_returns,
        historical_deposit_returns=fake_deposit_returns,
        n_simulations=5000,
        seed=123,
    )

    print(f"破產機率: {result.bankruptcy_probability:.1%}")
    print(f"30年後中位數餘額: {result.median_terminal_wealth:,.0f} 元")
    print(f"30年後平均餘額: {result.mean_terminal_wealth:,.0f} 元")

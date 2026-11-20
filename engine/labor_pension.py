"""
勞保老年年金估算模組(確定性計算,不涉及 AI)

公式依據(已於企劃階段查證):
- A式:平均月投保薪資 x 保險年資 x 0.775% + 3,000元
- B式:平均月投保薪資 x 保險年資 x 1.55%
  兩式取高者
- 提前請領(減額年金):每提前 1 年減 4%,最多提前 5 年
- 延後請領(展延年金):每延後 1 年增 4%,最多延後 5 年
- 法定請領年齡:65 歲(2026 年起)
"""

from dataclasses import dataclass

LEGAL_RETIREMENT_AGE = 65
MAX_EARLY_YEARS = 5
MAX_DEFER_YEARS = 5
EARLY_REDUCTION_PER_YEAR = 0.04   # 每提前一年減 4%
DEFER_BONUS_PER_YEAR = 0.04       # 每延後一年增 4%


@dataclass
class LaborPensionResult:
    monthly_pension: float          # 最終估計月退金(已套用提前/延後調整)
    formula_a: float                # A式金額(未調整)
    formula_b: float                # B式金額(未調整)
    base_formula: str                # "A" 或 "B",取高者
    adjustment_pct: float            # 提前/延後造成的調整比例(正=增加,負=減少)
    claim_age: int


def calculate_labor_pension(
    avg_insured_salary: float,
    insured_years: float,
    claim_age: int,
) -> LaborPensionResult:
    """
    計算勞保老年年金估計月退金。

    參數:
        avg_insured_salary: 最高 60 個月平均月投保薪資(元)
        insured_years: 保險年資(可有小數,例如 30.5 年)
        claim_age: 使用者實際請領年齡

    例外:
        ValueError: 當 claim_age 超出可請領範圍
            (法定年齡 65 歲,可提前最多 5 年、延後最多 5 年,
             即 60 到 70 歲之間)
    """
    min_age = LEGAL_RETIREMENT_AGE - MAX_EARLY_YEARS
    max_age = LEGAL_RETIREMENT_AGE + MAX_DEFER_YEARS

    if not (min_age <= claim_age <= max_age):
        raise ValueError(
            f"請領年齡需介於 {min_age} 至 {max_age} 歲之間"
            f"(法定年齡 {LEGAL_RETIREMENT_AGE} 歲,"
            f"可提前最多 {MAX_EARLY_YEARS} 年或延後最多 {MAX_DEFER_YEARS} 年),"
            f"收到的是 {claim_age} 歲"
        )

    # 兩式取高者(未調整前)
    formula_a = avg_insured_salary * insured_years * 0.00775 + 3000
    formula_b = avg_insured_salary * insured_years * 0.0155
    base_formula = "A" if formula_a >= formula_b else "B"
    base_amount = max(formula_a, formula_b)

    # 計算提前/延後造成的調整
    age_diff = claim_age - LEGAL_RETIREMENT_AGE
    if age_diff < 0:
        # 提前請領,減額
        adjustment_pct = age_diff * EARLY_REDUCTION_PER_YEAR  # 負值
    else:
        # 延後請領(或恰好法定年齡,age_diff=0 時無調整),展延
        adjustment_pct = age_diff * DEFER_BONUS_PER_YEAR  # 正值或 0

    monthly_pension = base_amount * (1 + adjustment_pct)

    return LaborPensionResult(
        monthly_pension=round(monthly_pension, 0),
        formula_a=round(formula_a, 0),
        formula_b=round(formula_b, 0),
        base_formula=base_formula,
        adjustment_pct=adjustment_pct,
        claim_age=claim_age,
    )


if __name__ == "__main__":
    # 快速自我測試:企劃書範例情境
    # 55歲、勞保繳了30年、平均投保薪資4萬5、65歲請領
    result = calculate_labor_pension(
        avg_insured_salary=45000,
        insured_years=30,
        claim_age=65,
    )
    print(f"採用公式: {result.base_formula}式")
    print(f"A式金額: {result.formula_a:,.0f} 元")
    print(f"B式金額: {result.formula_b:,.0f} 元")
    print(f"調整比例: {result.adjustment_pct:+.0%}")
    print(f"估計月退金: {result.monthly_pension:,.0f} 元")

    print("\n--- 延後兩年請領(67歲)比較 ---")
    result_delayed = calculate_labor_pension(
        avg_insured_salary=45000,
        insured_years=30,
        claim_age=67,
    )
    print(f"估計月退金: {result_delayed.monthly_pension:,.0f} 元")
    diff = result_delayed.monthly_pension - result.monthly_pension
    print(f"差額: {diff:+,.0f} 元/月")

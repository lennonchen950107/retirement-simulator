"""
基準參數 schema 與整合執行流程。

這個檔案是「計算核心」與「Prompt 工程(LLM 解析層)」之間的交界——
LLM 只需要輸出符合這個格式的 JSON,不用管底層怎麼算。

對應企劃書「七、技術架構詳述」7.2 基準參數格式。
"""

from dataclasses import dataclass, asdict, field
from typing import Any
import copy

from engine.labor_pension import calculate_labor_pension
from engine.simulation import run_monte_carlo
import numpy as np


# 基準參數範例(對應企劃書範例情境)
EXAMPLE_BASELINE_PARAMS: dict[str, Any] = {
    "current_age": 55,
    "retirement_age": 65,
    "labor_pension": {
        "insured_years": 30,
        "avg_insured_salary": 45000,
        "claim_type": "normal",  # normal / early / deferred,目前僅供標記,實際用 retirement_age 推算
    },
    "private_assets": {
        "total_amount": 8_000_000,
        "stock_pct": 0.6,
        "deposit_pct": 0.4,
    },
    "monthly_spending": 40_000,
    "simulation_years": 30,
}


def merge_diff(baseline: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """
    把 LLM 解析出的「參數變動」diff,合併進目前的基準參數。
    支援巢狀欄位(例如 {"labor_pension": {"insured_years": 32}})。
    不修改原本的 baseline,回傳一份新的合併結果。
    """
    merged = copy.deepcopy(baseline)

    def _deep_merge(target: dict, source: dict):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                _deep_merge(target[key], value)
            else:
                target[key] = value

    _deep_merge(merged, diff)
    return merged


def run_full_simulation(
    baseline_params: dict[str, Any],
    historical_stock_returns: np.ndarray,
    historical_deposit_returns: np.ndarray,
    n_simulations: int = 5000,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    整合「勞保估算」+「蒙地卡羅模擬」的完整流程,輸入基準參數,
    輸出一份結構化結果,可以直接當作 LLM 敘事層的 context 使用
    (對應企劃書 7.4「避免幻覺的 Context 設計」)。
    """
    pension = calculate_labor_pension(
        avg_insured_salary=baseline_params["labor_pension"]["avg_insured_salary"],
        insured_years=baseline_params["labor_pension"]["insured_years"],
        claim_age=baseline_params["retirement_age"],
    )

    sim = run_monte_carlo(
        initial_private_assets=baseline_params["private_assets"]["total_amount"],
        stock_pct=baseline_params["private_assets"]["stock_pct"],
        deposit_pct=baseline_params["private_assets"]["deposit_pct"],
        annual_labor_pension=pension.monthly_pension * 12,
        annual_spending=baseline_params["monthly_spending"] * 12,
        simulation_years=int(baseline_params["simulation_years"]),
        historical_stock_returns=historical_stock_returns,
        historical_deposit_returns=historical_deposit_returns,
        n_simulations=n_simulations,
        seed=seed,
    )

    return {
        "baseline_params": baseline_params,
        "labor_pension": {
            "monthly_pension": pension.monthly_pension,
            "base_formula": pension.base_formula,
            "adjustment_pct": pension.adjustment_pct,
        },
        "simulation": {
            "bankruptcy_probability": sim.bankruptcy_probability,
            "median_terminal_wealth": sim.median_terminal_wealth,
            "mean_terminal_wealth": sim.mean_terminal_wealth,
            # 每年的百分位數(長度 = simulation_years + 1),供 UI 畫 fan chart。
            # 不放進 format_context_for_llm(),LLM 不需要看到這些。
            "percentile_10": sim.percentile_10.tolist(),
            "percentile_50": sim.percentile_50.tolist(),
            "percentile_90": sim.percentile_90.tolist(),
        },
        # 完整 paths 不放進這個摘要(太大),需要時直接呼叫 run_monte_carlo
    }


# ---------------------------------------------------------------------------
# 參數驗證:UI 表單與 LLM 解析出的 JSON 都應該先過這一關,再丟進模擬
# ---------------------------------------------------------------------------

REQUIRED_PATHS = [
    ("current_age",),
    ("retirement_age",),
    ("labor_pension", "insured_years"),
    ("labor_pension", "avg_insured_salary"),
    ("private_assets", "total_amount"),
    ("private_assets", "stock_pct"),
    ("private_assets", "deposit_pct"),
    ("monthly_spending",),
    ("simulation_years",),
]


def _get_path(d: dict, path: tuple):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


def parse_llm_json(text: str) -> dict[str, Any]:
    """
    把 LLM 的原始輸出轉成 dict。容忍常見的格式問題:
    前後有說明文字、包在 ```json ... ``` 程式碼區塊裡。
    解析失敗時丟 ValueError(訊息是白話中文)。
    """
    import json
    import re

    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("LLM 輸出裡找不到 JSON 物件")
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 輸出不是合法的 JSON:{e}") from e
    if not isinstance(obj, dict):
        raise ValueError("LLM 輸出的 JSON 必須是物件(最外層是大括號)")
    return obj


_NUMBER_PATHS = [p for p in REQUIRED_PATHS]
_INT_PATHS = [("current_age",), ("retirement_age",), ("simulation_years",)]


def coerce_baseline(params: dict[str, Any]) -> dict[str, Any]:
    """
    把 LLM 常見的型別小瑕疵修正成程式需要的型別,回傳一份新的 dict,不改原本的。
    - "8,000,000" / "8000000" 這種數字字串 -> 數字
    - 年齡、模擬年數若是 65.0 這種整數值的小數 -> int(模擬迴圈需要整數)
    無法轉換的值原樣保留,交給 validate_baseline() 報錯。
    """
    out = copy.deepcopy(params)
    for path in _NUMBER_PATHS:
        parent = _get_path(out, path[:-1]) if len(path) > 1 else out
        if not isinstance(parent, dict) or path[-1] not in parent:
            continue
        v = parent[path[-1]]
        if isinstance(v, str):
            try:
                v = float(v.replace(",", "").strip())
            except ValueError:
                continue
        if isinstance(v, float) and v.is_integer() and path in _INT_PATHS:
            v = int(v)
        parent[path[-1]] = v
    return out


def validate_baseline(params: dict[str, Any]) -> list[str]:
    """
    檢查基準參數是否能安全丟進模擬。回傳錯誤訊息清單,空清單代表通過。
    錯誤訊息是給一般使用者看的白話中文。
    """
    errors: list[str] = []

    if isinstance(params, dict) and "missing" in params:
        return [f"資訊不足,還需要:{', '.join(map(str, params['missing']))}"]

    for path in REQUIRED_PATHS:
        if _get_path(params, path) is None:
            errors.append(f"缺少必要欄位:{'.'.join(path)}")
    if errors:
        return errors

    # 型別檢查:數字欄位必須真的是數字(不能是字串、布林),
    # 否則後面的比較運算會直接丟 TypeError,而不是給白話錯誤。
    for path in REQUIRED_PATHS:
        v = _get_path(params, path)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f"欄位 {'.'.join(path)} 必須是數字,目前是 {v!r}")
    if errors:
        return errors
    for path in _INT_PATHS:
        if _get_path(params, path) != int(_get_path(params, path)):
            errors.append(f"欄位 {'.'.join(path)} 必須是整數")
    if errors:
        return errors

    age = params["current_age"]
    ret_age = params["retirement_age"]
    if not (60 <= ret_age <= 70):
        errors.append("退休(請領勞保)年齡需介於 60 到 70 歲之間")
    if age <= 0 or age > 100:
        errors.append("目前年齡不合理")
    if age > ret_age:
        errors.append("目前年齡不能大於退休年齡")

    lp = params["labor_pension"]
    if lp["insured_years"] < 0:
        errors.append("勞保年資不能是負數")
    if lp["avg_insured_salary"] <= 0:
        errors.append("平均月投保薪資必須大於 0")

    pa = params["private_assets"]
    if pa["total_amount"] < 0:
        errors.append("私人資產不能是負數")
    if not (0 <= pa["stock_pct"] <= 1) or not (0 <= pa["deposit_pct"] <= 1):
        errors.append("股票與定存比例需介於 0 到 1 之間(例如 60% 要寫成 0.6)")
    elif abs(pa["stock_pct"] + pa["deposit_pct"] - 1.0) > 1e-6:
        errors.append("股票與定存比例相加必須等於 100%")

    if params["monthly_spending"] <= 0:
        errors.append("每月花費必須大於 0")
    if not (1 <= params["simulation_years"] <= 60):
        errors.append("模擬年數需介於 1 到 60 年")

    return errors


# ---------------------------------------------------------------------------
# 追問按鈕:按下後由「程式」產生參數變動,不經過 LLM
# ---------------------------------------------------------------------------

BUTTONS: dict[str, str] = {
    "delay2": "延後兩年退休",
    "advance2": "提前兩年退休",
    "stock_up": "股票比例提高 20 個百分點",
    "spend_down": "每月花費減 10%",
}


def button_diff(baseline: dict[str, Any], button_id: str) -> dict[str, Any]:
    """回傳按鈕對應的參數變動(diff),格式與 merge_diff() 相容。"""
    if button_id == "delay2":
        return {"retirement_age": min(baseline["retirement_age"] + 2, 70)}
    if button_id == "advance2":
        return {"retirement_age": max(baseline["retirement_age"] - 2, 60)}
    if button_id == "stock_up":
        stock = min(baseline["private_assets"]["stock_pct"] + 0.2, 1.0)
        return {"private_assets": {"stock_pct": round(stock, 4),
                                   "deposit_pct": round(1.0 - stock, 4)}}
    if button_id == "spend_down":
        return {"monthly_spending": round(baseline["monthly_spending"] * 0.9)}
    raise ValueError(f"未知的按鈕:{button_id}")


def format_context_for_llm(result: dict[str, Any]) -> str:
    """
    把模擬結果摘要轉成企劃書 7.4 建議的簡短文字格式,
    作為每次呼叫 LLM 時附上的 context,避免 LLM 憑空編造數字。
    """
    bp = result["baseline_params"]
    lp = result["labor_pension"]
    sim = result["simulation"]

    return f"""(所有金額單位:新台幣元)
目前情境:
- 退休年齡:{bp['retirement_age']}
- 勞保估計月退:{lp['monthly_pension']:,.0f}
- 私人資產:{bp['private_assets']['total_amount']:,.0f}(股{bp['private_assets']['stock_pct']:.0%}/定存{bp['private_assets']['deposit_pct']:.0%})
- 每月花費:{bp['monthly_spending']:,.0f}

最新模擬結果:
- 破產機率:{sim['bankruptcy_probability']:.1%}
- {bp['simulation_years']}年後中位數餘額:{sim['median_terminal_wealth']:,.0f}
- {bp['simulation_years']}年後平均餘額:{sim['mean_terminal_wealth']:,.0f}"""


def load_baseline_from_llm(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """
    舊版入口,保留相容:回傳 (params 或 None, 錯誤清單)。
    建議改用 interpret_llm_output(),因為它能分辨「該追問」與「格式錯誤」。
    """
    o = interpret_llm_output(text)
    if o.kind == "ok":
        return o.params, []
    return None, ([o.followup] if o.kind == "missing" else o.errors)


FIELD_LABELS: dict[str, str] = {
    "current_age": "目前年齡",
    "retirement_age": "預計退休年齡",
    "insured_years": "勞保保險年資",
    "avg_insured_salary": "平均月投保薪資",
    "total_amount": "私人資產總額",
    "stock_pct": "股票比例",
    "deposit_pct": "定存比例",
    "monthly_spending": "退休後每月花費",
    "simulation_years": "模擬年數",
}


def canonical_field(name: str) -> str | None:
    """
    把 LLM 回報的欄位名稱統一成英文欄位名。接受:
    insured_years、labor_pension.insured_years、勞保年資、勞保保險年資、投保薪資、每月花費……
    股票比例與定存比例都回傳各自的名稱;認不得時回傳 None。
    """
    n = str(name).strip()
    last = n.split(".")[-1].strip().lower()
    if last in FIELD_LABELS:
        return last
    if "退休" in n and ("年齡" in n or "歲" in n):
        return "retirement_age"
    if "投保" in n and ("薪" in n or "額" in n):
        return "avg_insured_salary"
    if "年資" in n:
        return "insured_years"
    if any(k in n for k in ("花費", "支出", "生活費", "開銷")):
        return "monthly_spending"
    if "股票" in n:
        return "stock_pct"
    if "定存" in n:
        return "deposit_pct"
    if any(k in n for k in ("資產", "存款", "總額", "本金")):
        return "total_amount"
    if "模擬" in n:
        return "simulation_years"
    if "年齡" in n or "歲" in n:
        return "current_age"
    return None


def describe_missing(missing: list[str]) -> list[str]:
    """把 LLM 回報的缺欄位名稱轉成中文標籤(去除重複);認不得的名稱原樣保留。"""
    out: list[str] = []
    for m in missing:
        c = canonical_field(m)
        label = FIELD_LABELS[c] if c else str(m)
        if label not in out:
            out.append(label)
    return out


@dataclass
class ParseOutcome:
    """
    Prompt 一的輸出該怎麼處理,只有三種結果:
      kind == "ok"      -> params 可以直接丟進模擬
      kind == "missing" -> 資料不足,不能模擬,要拿 followup 去問使用者
      kind == "invalid" -> 格式或數值有問題,errors 是白話原因
    """
    kind: str
    params: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)   # 中文欄位名稱
    errors: list[str] = field(default_factory=list)

    @property
    def followup(self) -> str:
        """kind == "missing" 時,給使用者看的追問句。"""
        return f"還需要這些資訊才能試算:{'、'.join(self.missing)}。請補充(說法模糊的請改成確切數字)。"


def interpret_llm_output(text: str) -> ParseOutcome:
    """
    LLM 原始輸出 -> ParseOutcome。永遠不丟例外。
    - 明確回報 {"missing": [...]} -> missing
    - 必填欄位缺漏或是 null(模型沒照規則用 missing)-> 也視為 missing,不是錯誤
    - 欄位齊全但數值不合理(例如退休年齡 75、比例 60/40)-> invalid
    """
    try:
        obj = parse_llm_json(text)
    except ValueError as e:
        return ParseOutcome("invalid", errors=[str(e)])

    if "missing" in obj:
        names = obj["missing"] if isinstance(obj["missing"], list) else []
        return ParseOutcome("missing", missing=describe_missing(names) or ["更多資訊"])

    unsupported = obj.get("unsupported")
    if isinstance(unsupported, list) and unsupported:
        names = "、".join(map(str, unsupported))
        return ParseOutcome("invalid", errors=[
            f"目前只支援股票與定存兩種資產,不支援:{names}。"
            "請把資產調整成只有股票與定存(例如只填這兩類的金額與比例)再試算。"])

    params = coerce_baseline(obj)
    absent = [".".join(p) for p in REQUIRED_PATHS if _get_path(params, p) is None]
    if absent:
        return ParseOutcome("missing", missing=describe_missing(absent))

    errors = validate_baseline(params)
    if errors:
        return ParseOutcome("invalid", errors=errors)
    return ParseOutcome("ok", params=params)


def process_llm_output(
    raw: str,
    historical_stock_returns: np.ndarray,
    historical_deposit_returns: np.ndarray,
    n_simulations: int = 300,
    seed: int | None = 1,
) -> dict[str, Any]:
    """
    給測試用的一步到位函式:LLM 原始輸出 -> 判斷 -> (只有 ok 才)模擬。永遠不丟例外。

    回傳 dict,先看 "status":
      "ok"      -> "result" 是模擬結果(可接 format_context_for_llm)
      "missing" -> 不模擬;"followup" 是要問使用者的話(這是正確行為,不是錯誤)
      "invalid" -> 不模擬;"errors" 是白話原因
      "crashed" -> 通過驗證卻在模擬時出錯(引擎的問題,"error" 有訊息,請回報給 Lennon)
    """
    o = interpret_llm_output(raw)
    if o.kind == "missing":
        return {"status": "missing", "followup": o.followup, "missing": o.missing}
    if o.kind == "invalid":
        return {"status": "invalid", "errors": o.errors}
    try:
        result = run_full_simulation(
            o.params, historical_stock_returns, historical_deposit_returns,
            n_simulations=n_simulations, seed=seed,
        )
    except Exception as e:  # 驗證過了還出錯 = 引擎 bug,明確標示而不是讓測試程式崩潰
        return {"status": "crashed", "error": f"{type(e).__name__}: {e}", "params": o.params}
    return {"status": "ok", "params": o.params, "result": result}

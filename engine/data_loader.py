"""
歷史報酬率 CSV 的讀取與驗證(角色二交付 data/taiex_annual_returns.csv 後,由這裡把關)。

格式:兩欄 year、return,報酬率是「小數」(12.3% 要寫成 0.123)。
錯誤一律丟 DataError,訊息是白話中文,app 會直接顯示給隊友看。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np


class DataError(ValueError):
    pass


@dataclass
class ReturnsData:
    returns: np.ndarray
    first_year: int
    last_year: int
    warnings: list[str] = field(default_factory=list)


MIN_ROWS = 10          # 少於這個年數,重抽樣沒有意義
SHORT_HISTORY = 20     # 少於這個年數,提醒歷史偏短
MAX_ABS_RETURN = 3.0   # 單年 ±300% 以上幾乎一定是單位寫錯(把 12.3 當成 12.3%)


def load_returns_csv(path: str) -> ReturnsData:
    import pandas as pd

    if not os.path.exists(path):
        raise DataError(f"找不到檔案:{path}")
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")   # utf-8-sig:相容 Excel 存出的 BOM
    except Exception as e:
        raise DataError(f"CSV 讀取失敗:{e}") from e

    df.columns = [str(c).strip().lower() for c in df.columns]
    lacking = {"year", "return"} - set(df.columns)
    if lacking:
        raise DataError(f"CSV 缺少欄位:{', '.join(sorted(lacking))}(需要 year 與 return 兩欄,目前有:{', '.join(df.columns)})")

    years = pd.to_numeric(df["year"], errors="coerce")
    rets = pd.to_numeric(df["return"], errors="coerce")
    bad_rows = df.index[years.isna() | rets.isna()].tolist()
    if bad_rows:
        shown = ", ".join(str(i + 2) for i in bad_rows[:5])   # +2:標題列 + 從 1 起算
        raise DataError(
            f"第 {shown} 列(Excel 列號)的 year 或 return 不是數字或是空白。"
            "提醒:return 不能寫成「12.3%」,請寫成 0.123。"
        )
    if years.duplicated().any():
        dup = int(years[years.duplicated()].iloc[0])
        raise DataError(f"year 欄位有重複的年份:{dup}")
    if len(df) < MIN_ROWS:
        raise DataError(f"歷史資料只有 {len(df)} 年,至少需要 {MIN_ROWS} 年")
    if (rets <= -1).any():
        raise DataError("有某一年報酬率 <= -100%(return <= -1),請檢查資料")
    if rets.abs().max() > MAX_ABS_RETURN:
        raise DataError(
            f"有某一年報酬率絕對值超過 {MAX_ABS_RETURN:.0f}(= {MAX_ABS_RETURN:.0%}),"
            "很可能把百分比當成小數,請把 12.3 改成 0.123"
        )

    order = np.argsort(years.to_numpy())
    years_sorted = years.to_numpy()[order].astype(int)
    rets_sorted = rets.to_numpy()[order].astype(float)

    warnings: list[str] = []
    if len(df) < SHORT_HISTORY:
        warnings.append(f"歷史資料只有 {len(df)} 年,偏短,模擬結果的代表性有限")
    if (np.diff(years_sorted) != 1).any():
        warnings.append("年份不連續。模擬會把相鄰的列當成連續年份,順序風險可能失真")

    return ReturnsData(rets_sorted, int(years_sorted[0]), int(years_sorted[-1]), warnings)

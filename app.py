"""
退休提領順序風險試算器 — Streamlit 介面

執行方式(在 retirement_simulator/ 這層目錄下):
    streamlit run app.py

結構:
    左側表單  -> 基準參數 -> validate_baseline() -> run_full_simulation()
    結果區    -> 指標、fan chart、白話解讀(llm_hooks.narrate,目前是佔位)
    追問按鈕  -> button_diff() 由程式產生參數變動 -> merge_diff() -> 重跑 -> 前後比較

所有數字都由 engine/ 計算,這個檔案與 LLM 都不做任何數字計算。
"""

from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from engine.data_loader import DataError, load_returns_csv
from engine.schema import (
    BUTTONS,
    button_diff,
    describe_missing,
    format_context_for_llm,
    merge_diff,
    run_full_simulation,
    validate_baseline,
)
import llm_hooks
from llm_hooks import LLMUnavailable, narrate_with_status, parse_description, parse_followup

# --- 設定 -------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TAIEX_CSV = os.path.join(BASE_DIR, "data", "taiex_annual_returns.csv")
DEPOSIT_RATE = 0.015      # 定存利率固定假設 1.5%(限制說明會註明)
N_SIMULATIONS = 5000
SEED = 123                # 固定種子:追問前後用同一組隨機數,差異才來自參數而不是運氣

def _pick_cjk_font() -> str | None:
    """找一個可用的中文字型;雲端主機(Linux)可能沒有,此時圖表改用英文標籤,避免整排方框。"""
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ["Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans TC",
                 "Noto Sans CJK SC", "Heiti TC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "SimHei", "Arial Unicode MS"]:
        if name in have:
            return name
    return None


CJK_FONT = _pick_cjk_font()
plt.rcParams["font.sans-serif"] = [CJK_FONT, "DejaVu Sans"] if CJK_FONT else ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

st.set_page_config(page_title="退休提領順序風險試算器", layout="wide")


# --- 資料 -------------------------------------------------------------------

def csv_mtime() -> float:
    """CSV 的修改時間。當成快取鍵,檔案一換就自動重讀,不用重啟 streamlit。"""
    return os.path.getmtime(TAIEX_CSV) if os.path.exists(TAIEX_CSV) else 0.0


@st.cache_data
def _load_returns_cached(mtime: float):
    """
    讀真實台股年報酬率,回傳 (股票報酬, 定存報酬, 是否真實資料, 提示訊息清單)。
    檔案不存在或格式有問題時退回示範假資料,並把原因放進提示訊息(畫面會顯示)。
    """
    notes: list[str] = []
    if os.path.exists(TAIEX_CSV):
        try:
            data = load_returns_csv(TAIEX_CSV)
            notes = list(data.warnings)
            notes.insert(0, f"歷史資料:{data.first_year}–{data.last_year} 年,共 {len(data.returns)} 年")
            return data.returns, np.full(len(data.returns), DEPOSIT_RATE), True, notes
        except DataError as e:
            notes.append(f"data/taiex_annual_returns.csv 有問題,已改用示範假資料:{e}")
    else:
        notes.append("找不到 data/taiex_annual_returns.csv,目前使用示範假資料")
    rng = np.random.default_rng(42)
    stock = rng.normal(0.08, 0.20, size=40)
    return stock, np.full(40, DEPOSIT_RATE), False, notes


def load_returns():
    return _load_returns_cached(csv_mtime())


def run(params: dict) -> dict:
    stock, deposit, _, _ = load_returns()
    return run_full_simulation(
        params, stock, deposit, n_simulations=N_SIMULATIONS, seed=SEED
    )


# --- 狀態 -------------------------------------------------------------------

for key, default in {
    "baseline": None,       # 目前使用的基準參數
    "result": None,         # 最新模擬結果
    "previous": None,       # 追問前的結果(按過按鈕才有)
    "narration": "",        # 白話解讀(算一次就存,避免每次重繪都呼叫 LLM)
    "narration_reason": "",  # 退回模板的原因(顯示給使用者,方便判斷是額度、金鑰還是其他問題)
    "narration_source": "template",  # "llm" 或 "template"(LLM 不可用時的程式模板)
    "last_button": None,
    "last_prefix": "如果",   # 結果標題前綴:按鈕是「如果…」,自由追問是「調整:…」
    "pending_text": "",     # 一句話描述資料不足時,累積到下一次補充(最多 500 字)
    "messages": [],         # 要顯示給使用者的錯誤或提示
}.items():
    st.session_state.setdefault(key, default)


def store_result(params: dict, result: dict, previous: dict | None, label: str | None, prefix: str = "如果"):
    st.session_state.baseline = params
    st.session_state.last_prefix = prefix
    st.session_state.result = result
    st.session_state.previous = previous
    st.session_state.last_button = label
    # 模擬年數不同時,「N 年後的餘額」不能直接比較,白話解說只講調整後的結果
    comparable = bool(previous) and previous["baseline_params"]["simulation_years"] == params["simulation_years"]
    prev_ctx = format_context_for_llm(previous) if comparable else None
    text, source = narrate_with_status(format_context_for_llm(result), prev_ctx)
    st.session_state.narration = text
    st.session_state.narration_source = source
    st.session_state.narration_reason = llm_hooks.LAST_REASON if source == "template" else ""


def apply_button(button_id: str):
    """追問按鈕的 callback:程式產生參數變動 -> 驗證 -> 重跑 -> 前後比較。"""
    base = st.session_state.baseline
    new_params = merge_diff(base, button_diff(base, button_id))
    st.session_state.messages = []
    if new_params == base:
        st.session_state.messages = ["已經在可調整範圍的上限或下限,沒有變動。"]
        return
    errors = validate_baseline(new_params)
    if errors:
        st.session_state.messages = errors
        return
    store_result(new_params, run(new_params), st.session_state.result, BUTTONS[button_id])


def apply_followup():
    """自由追問的 callback:一句話 -> LLM 只判斷要改什麼 -> 程式算新值並驗證 -> 重跑 -> 前後比較。"""
    text = (st.session_state.get("followup_text") or "").strip()
    st.session_state.messages = []
    try:
        outcome = parse_followup(text, st.session_state.baseline)
    except LLMUnavailable as e:
        st.session_state.messages = [f"AI 追問目前無法使用({e}),請改用下方的按鈕。"]
        return
    except ValueError as e:
        st.session_state.messages = [str(e)]
        return
    if outcome.kind != "change":
        st.session_state.messages = outcome.user_messages
        return
    store_result(outcome.params, run(outcome.params), st.session_state.result, outcome.summary, prefix="調整:")


# --- 左側輸入 ---------------------------------------------------------------

def run_new_baseline(params: dict):
    """初次輸入(表單或自由文字)共用:驗證 -> 模擬 -> 存結果。"""
    errors = validate_baseline(params)
    st.session_state.messages = errors
    if not errors:
        store_result(params, run(params), None, None)


def handle_free_text(text: str):
    """
    自由文字初次輸入:LLM 解析 -> 資料不足就追問(並累積使用者的補充)-> 驗證 -> 模擬。
    任何失敗都顯示白話訊息,不當掉。
    """
    st.session_state.messages = []
    text = (text or "").strip()
    combined = (st.session_state.pending_text + "\n" + text).strip() if st.session_state.pending_text else text
    if combined and len(combined) <= 500:
        st.session_state.pending_text = combined   # 先存起來:不管這次成不成功,使用者說過的話都不會消失
    try:
        outcome = parse_description(combined)
    except LLMUnavailable as e:
        st.session_state.messages = [f"AI 解析目前無法使用({e}),請改用「表單」輸入。"]
        return
    except ValueError as e:
        st.session_state.messages = [f"{e}。可以換個說法再試一次,或改用「表單」輸入。"]
        return

    if outcome.kind == "missing":
        st.session_state.messages = [outcome.followup]   # pending_text 已保留,使用者只要補不足的部分
    elif outcome.kind == "invalid":
        st.session_state.pending_text = ""   # 內容不合法:附加補充說不清楚,清掉舊描述,請使用者完整重述
        st.session_state.messages = list(outcome.errors) + ["這段描述已清除,請修正後重新完整描述一次。"]
    else:
        st.session_state.pending_text = ""
        run_new_baseline(outcome.params)


with st.sidebar:
    st.header("你的退休情況")
    mode = st.radio("輸入方式", ["表單", "一句話描述"], horizontal=True)

    if mode == "表單":
        with st.form("inputs"):
            current_age = st.number_input("目前年齡", 18, 100, 55)
            retirement_age = st.slider("預計退休(請領勞保)年齡", 60, 70, 65)
            insured_years = st.number_input("勞保保險年資(年)", 0.0, 60.0, 30.0, step=0.5)
            avg_salary = st.number_input("平均月投保薪資(新台幣元)", 0, 1_000_000, 45_000, step=1_000)
            total_amount = st.number_input("私人資產總額(新台幣元)", 0, 1_000_000_000, 8_000_000, step=100_000)
            stock_pct = st.slider("股票比例(%)(其餘為定存)", 0, 100, 60, step=5)
            monthly_spending = st.number_input("退休後每月花費(新台幣元)", 0, 1_000_000, 40_000, step=1_000)
            simulation_years = st.slider("模擬年數", 10, 40, 30)
            submitted = st.form_submit_button("開始試算", type="primary")

        if submitted:
            run_new_baseline({
                "current_age": int(current_age),
                "retirement_age": int(retirement_age),
                "labor_pension": {
                    "insured_years": float(insured_years),
                    "avg_insured_salary": int(avg_salary),
                    "claim_type": "normal",
                },
                "private_assets": {
                    "total_amount": int(total_amount),
                    "stock_pct": stock_pct / 100,
                    "deposit_pct": round(1 - stock_pct / 100, 4),
                },
                "monthly_spending": int(monthly_spending),
                "simulation_years": int(simulation_years),
            })
    else:
        with st.form("free_text", clear_on_submit=True):
            description = st.text_area(
                "補充上面缺少的資訊" if st.session_state.pending_text else "用一句話描述你的狀況",
                placeholder="例如:我 55 歲,勞保繳了 30 年,平均投保薪資 4 萬 5,存了 800 萬,"
                            "股票佔六成,想 65 歲退休,退休後每月花 4 萬",
                height=160,
                key="free_text_input",   # 固定 key:標籤改變時輸入框不會被當成新元件而重置
            )
            submitted_text = st.form_submit_button("解析並試算", type="primary")
        if st.session_state.pending_text:
            st.caption("已記住你前面說過的:" + st.session_state.pending_text.replace("\n", " / "))
            if st.button("清除重來"):
                st.session_state.pending_text = ""
                st.rerun()
        if submitted_text:
            with st.spinner("AI 正在解析你的描述……"):
                handle_free_text(description)


# --- 主畫面 -----------------------------------------------------------------

st.title("退休提領順序風險試算器")
st.caption("依台股歷史報酬率重抽樣模擬,試算退休後資產被領光的風險。結果不代表未來績效,也不構成投資建議。所有金額單位皆為新台幣(NT$)。")

_, _, is_real, data_notes = load_returns()
if not is_real:
    st.warning("目前使用的是示範用假資料,數字僅供測試介面。\n\n" + "\n\n".join(data_notes))
else:
    st.caption(" ・ ".join(data_notes))

for msg in st.session_state.messages:
    st.error(msg)

result = st.session_state.result
if result is None:
    st.info("請在左側填寫資料後按「開始試算」。")
    st.stop()

previous = st.session_state.previous
sim, pension = result["simulation"], result["labor_pension"]
bp = result["baseline_params"]

if st.session_state.last_button:
    st.subheader(f"{st.session_state.last_prefix}{st.session_state.last_button}")
    st.caption("與調整前相比(兩次模擬使用同一組隨機數,差異來自參數變動)。")

# 指標
c1, c2, c3 = st.columns(3)
bankruptcy = sim["bankruptcy_probability"]
delta_b = None
if previous:
    diff_b = (bankruptcy - previous["simulation"]["bankruptcy_probability"]) * 100
    delta_b = "無變化" if abs(diff_b) < 0.05 else f"{diff_b:+.1f} 個百分點"
c1.metric("資產被領光的機率", f"{bankruptcy:.1%}", delta_b,
          delta_color="off" if delta_b == "無變化" else "inverse")

delta_p = None
if previous:
    delta_p = f"{pension['monthly_pension'] - previous['labor_pension']['monthly_pension']:+,.0f} 元"
c2.metric("勞保估計月退(新台幣)", f"NT$ {pension['monthly_pension']:,.0f}", delta_p)

median_w = sim["median_terminal_wealth"]
same_horizon = bool(previous) and previous["baseline_params"]["simulation_years"] == bp["simulation_years"]
delta_m = None
if same_horizon:
    delta_m = f"{(median_w - previous['simulation']['median_terminal_wealth']) / 10_000:+,.0f} 萬"
c3.metric(f"{bp['simulation_years']} 年後資產中位數(新台幣)", f"NT$ {median_w / 10_000:,.0f} 萬", delta_m)

if previous and not same_horizon:
    st.caption(f"模擬年數不同({previous['baseline_params']['simulation_years']} 年 → {bp['simulation_years']} 年):"
               "不同期間的資產中位數不直接比較;破產機率是各自模擬期間內被領光的比例。")

# fan chart
years = np.arange(bp["simulation_years"] + 1)
fig, ax = plt.subplots(figsize=(9, 4))
ax.fill_between(years, np.array(sim["percentile_10"]) / 10_000,
                np.array(sim["percentile_90"]) / 10_000, alpha=0.25, label="10%~90% 區間" if CJK_FONT else "10%-90% range")
ax.plot(years, np.array(sim["percentile_50"]) / 10_000, linewidth=2, label="中位數" if CJK_FONT else "Median")
if previous:
    prev_p50 = np.array(previous["simulation"]["percentile_50"]) / 10_000
    ax.plot(np.arange(len(prev_p50)), prev_p50,   # 調整前的年數可能不同,用自己的橫軸
            linestyle="--", color="gray", label="調整前的中位數" if CJK_FONT else "Median before adjustment")
ax.set_xlabel("退休後第幾年" if CJK_FONT else "Years after retirement")
ax.set_ylabel("私人資產(新台幣萬元)" if CJK_FONT else "Private assets (NT$ 10,000)")
ax.set_ylim(bottom=0)
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
st.pyplot(fig)
plt.close(fig)

# 白話解讀
st.subheader("白話解讀")
st.info(st.session_state.narration)
if st.session_state.narration_source == "template":
    reason = st.session_state.narration_reason
    st.caption("這段文字由程式模板產生(AI 解讀目前不可用,或被數字檢查擋下)。數字皆來自上方模擬結果。"
               + (f" 原因:{reason}" if reason else ""))

# 追問按鈕
st.subheader("想知道如果……")
cols = st.columns(len(BUTTONS))
for col, (bid, label) in zip(cols, BUTTONS.items()):
    col.button(label, key=bid, on_click=apply_button, args=(bid,), use_container_width=True)

with st.form("followup_form", clear_on_submit=True):
    st.text_input(
        "或直接用一句話追問",
        key="followup_text",
        max_chars=200,
        placeholder="例如:如果晚三年退休、每月花費減少 5000、股票比例改成 40%",
    )
    st.form_submit_button("送出追問", on_click=apply_followup)
st.caption("追問只會調整上面這些參數:AI 只判斷你想改什麼,新數值由程式計算;看不懂或超出範圍時不會改動結果。")

with st.expander("模型假設與限制"):
    st.markdown(
        """
- **起點是退休當天**:私人資產總額視為退休時的金額;「目前年齡」只用來檢查輸入合理,不模擬退休前的累積或提領。
- **不含通膨**:每月花費與勞保月退在整段模擬期間都固定不變(名目金額),長期會低估實際花費的成長。
- **歷史重抽樣**:每條路徑都是整段歷史年份的連續視窗(首尾相接),所以不同路徑的數量受歷史資料年數限制,不是無限多種。
- **股票用台股歷史報酬**,為價格指數報酬、**不含股利**(實際報酬可能被低估);**定存利率固定 1.5%**;沒有稅、手續費、醫療支出或遺產考量。
- **只涵蓋股票與定存兩種資產**,以及一般老年年金情境;其他資產(房地產等)與其他請領類型不支援。
- 勞保月退依年資與投保薪資用公式估算,提前或延後請領每年增減 4%;勞保結餘與制度改革風險不納入。
- 勞保年金超過每月花費的部分不會存進私人資產(視為用不到)。
        """
    )

# 開發者資訊(給隊友對 prompt 用,demo 時可收起來)
with st.expander("開發者資訊:目前參數與給 LLM 的 context"):
    st.json(bp)
    st.code(format_context_for_llm(result), language="text")

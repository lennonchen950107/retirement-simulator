# 整合規格(給角色二、角色三)

所有人先在 `retirement_simulator/` 目錄執行 `python tests/run_all.py`,看到「全部通過」再開始。

## 角色二:資料與 API

**1. `data/taiex_annual_returns.csv`**
- 兩欄:`year`、`return`;報酬率是小數(12.3% 寫成 `0.123`,不要寫 `12.3` 或 `12.3%`)
- 至少 10 年,建議 20 年以上;年份不重複、連續
- 放進去後不用重啟,畫面會自動重讀;格式有問題時畫面會用白話說明哪裡錯,並退回假資料
- 驗證規則在 `engine/data_loader.py`;自己先檢查:`python -c "from engine.data_loader import load_returns_csv as f; print(f('data/taiex_annual_returns.csv'))"`

**2. `llm_client.py`**(專案裡已經有一份,角色二接手驗證與維護)
- 介面只有一個函式:`complete(system, user) -> str`。**角色二任務書的 `safe_json_call` 也保留著,但 app 用的是 `complete`**。
  如果自己另寫 llm_client.py,必須保留 `complete`,否則 app 會判定 AI 不可用而退回表單與模板。
- 設定只從環境變數讀:`LLM_API_KEY`(必填)、`LLM_MODEL`(預設 gemini-2.5-flash)、`LLM_TIMEOUT_S`(預設 20)
- 失敗時丟例外就好,`llm_hooks.py` 會接住,介面自動退回表單與程式模板
- **這份對 Gemini 的呼叫尚未用真實金鑰實測**:先跑任務書的 `tests/llm_ping.py`,再跑 `python tests/test_llm_client.py`,
  然後在 app 的「一句話描述」實際用一次
- 每次呼叫會記一行到 `llm_usage.jsonl`(呼叫別 parse/explain、token 數、秒數、成功與否),**不含任何對話內容**。
  角色四的 Cost & Latency 直接用這個檔案算平均
- 不要覆蓋 `tests/demo.py`:專案裡的 demo.py 和原版相同,角色二若已照任務書改好自己的,保留自己的那份即可

## 角色三:Prompt

**1. 檔案放 `prompts/`,版本號寫進檔名**:`parse_v2.txt`、`explain_v2.txt`……程式自動使用版本號最大的。
目前的 `parse_v1.txt`、`explain_v1.txt` 只是任務書的起點版,請在此基礎上改。

**2. Prompt 一(parse)的輸出怎麼測**

模型輸出只有三種結果,**測試流程一定要先分辨,不能把輸出直接丟進 `run_full_simulation()`**
(`{"missing": [...]}` 不是完整參數,直接丟會出現 `KeyError`):

```python
from engine.schema import interpret_llm_output
o = interpret_llm_output(模型原始輸出)      # 永遠不丟例外
# o.kind == "ok"      -> o.params 可直接模擬
# o.kind == "missing" -> 資料不足,不模擬;用 o.followup 追問使用者(這是正確行為,不是錯誤)
# o.kind == "invalid" -> 格式或數值有問題;o.errors 是白話原因
```

**正式測試(角色四的 A~D 類 16 句,自動評分):**

```bash
python tests/parse_suite.py sheet                       # 產生測試句清單與貼答案的檔案
python tests/parse_suite.py grade parse_responses.txt   # 評分
python tests/parse_suite.py live                        # 有 API 金鑰後:自動呼叫並評分
```
評分會逐欄比對數值,並抓出:缺資料時亂猜、擅自修改使用者的數字(例如把 50 歲退休改成 65)、
把「五十幾歲」當成精確數字、把工作年資當成勞保年資。門檻是 16 句至少 15 句。

批次測試:把每筆模型輸出存進一個文字檔,筆與筆之間用單獨一行 `---` 分隔,第一行可寫 `#expect: ok|missing|invalid`:

```bash
python tests/check_parse_outputs.py my_outputs.txt
```
會逐筆列出結果,並統計符合期望的比例(任務書要求 90%)。範例格式見該檔案開頭的說明。

已容忍:```json 程式碼區塊、前後說明文字、`"8,000,000"` 這種數字字串、`65.0`、
用 `null` 或直接省略欄位來代替 `missing`(都視為 missing,該追問)。
**會判為 invalid**:比例寫成 60/40(要 0.6/0.4)、退休年齡不在 60–70。

**模型該做的**:缺資料時輸出 `{"missing": ["insured_years", ...]}`,不要猜,也不要自己補預設值。
介面會把使用者之後的補充和前面說過的話累積起來一起再送給模型,所以 prompt 只需要處理「一段完整描述」。

**3. Prompt 二(explain)的數字檢查已經在程式裡**
輸出出現 context 沒有的數字時,程式會丟掉輸出、改用模板,並在畫面標示。要自己統計時:
```python
from engine.guard import numbers_not_in_context
print(numbers_not_in_context(輸出, context))   # 應為 set()
```

**4. 單句測試用的假資料與跑模擬**
```python
import numpy as np
from engine.schema import run_full_simulation
st, dep = np.random.default_rng(0).normal(0.08, 0.2, 40), np.full(40, 0.015)
run_full_simulation(params, st, dep, n_simulations=300, seed=1)   # 注意:要帶歷史報酬率這兩個參數
```

## 所有人
- 不要修改 `engine/`,有問題回報給 Lennon
- 測試時不要用真實的個人財務資料
- 不要轉傳 `Project_Brief.md`

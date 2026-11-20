# 退休提領順序風險試算器

勞保年金估算 + 蒙地卡羅(block bootstrap)提領模擬 + Streamlit 介面。
所有數字都由 `engine/` 用確定性程式計算,LLM 只負責參數解析與白話敘事,不做任何計算。

## 目錄結構

```
retirement_simulator/
├── app.py                 # Streamlit 介面(表單 / 一句話描述、結果、fan chart、追問按鈕、假設與限制)
├── llm_hooks.py           # LLM 接點:白話解讀(含數字檢查與程式模板後備)、自由文字解析
├── llm_client.py          # LLM 呼叫層的介面約定(角色二交付後取代)
├── prompts/               # prompt 文字檔,版本號在檔名(parse_v1.txt…),自動用最大版本
├── engine/
│   ├── labor_pension.py   # 勞保老年年金估算(A式/B式、提前/延後調整)
│   ├── simulation.py      # 蒙地卡羅模擬(歷史報酬重抽樣)
│   ├── schema.py          # 基準參數、驗證、LLM JSON 容錯解析、追問按鈕、LLM context 格式化
│   ├── data_loader.py     # 歷史報酬率 CSV 讀取與驗證
│   └── guard.py           # 檢查 LLM 輸出的數字是否都來自 context
├── data/                  # 放 taiex_annual_returns.csv(角色二產出)
├── tests/                 # run_all.py 一次跑完;test_engine / test_llm_json / test_hooks / test_app
├── INTEGRATION.md         # 給角色二、角色三的交付規格
└── requirements.txt
```

## 怎麼執行

在 `retirement_simulator/` 這層目錄的終端機:

```bash
pip install -r requirements.txt
streamlit run app.py          # 開啟介面(瀏覽器會自動跳出)
python tests/run_all.py       # 跑全部測試(不需要 API 金鑰)
python tests/demo.py          # 命令列端對端示範
```

## 資料來源

`app.py` 找 `data/taiex_annual_returns.csv`(兩欄 `year`、`return`,報酬率是小數),格式由 `engine/data_loader.py` 驗證。
找不到或格式有問題就退回示範假資料,畫面上會出現黃色警告並說明原因。換檔案不用重啟。定存利率固定假設 1.5%(`app.py` 的 `DEPOSIT_RATE`)。

## LLM 不可用時會怎樣

- 白話解讀:退回程式模板(數字原封不動取自模擬結果),畫面會標示
- LLM 輸出出現模擬結果沒有的數字:同樣丟掉、退回模板
- 一句話描述輸入:顯示白話訊息,請使用者改用表單

## 模型假設與限制(app 內也有同樣的說明)

起點是退休當天、不含通膨、歷史重抽樣的路徑數受資料年數限制、股票為價格指數報酬不含股利、定存利率固定、只涵蓋股票與定存與一般老年年金、不含稅與醫療支出。細節見 app 的「模型假設與限制」。

## 各角色接點

見 `INTEGRATION.md`。

## 自由追問(結果出來後,用一句話調整條件)

- 結果區下方的輸入框:「如果晚兩年退休」「每月花費減少 5000」「股票比例改成 40%」。
- 分工:AI(`prompts/followup_v1.txt`)只輸出「改哪個欄位、怎麼改」(set / add / multiply),
  **新數值由 `engine/followup.py` 計算**,再走 `validate_baseline()` 與引擎重算,所以 AI 仍然不碰計算。
- 看不懂(unclear)、超出範圍(out_of_scope)、調整後不合理(例如退休年齡 73)時,不改參數、不重算,顯示白話說明。
- 可調整欄位:退休年齡、每月花費、私人資產總額、股票/定存比例、勞保年資、平均月投保薪資、模擬年數。
- 只做「調整參數」,不做開放式問答(例如「為什麼會這樣」);這類句子會被判為超出範圍。
- 測試:`python tests/test_followup.py`(邏輯)、`python tests/followup_suite.py live`(真模型 18 句自動評分,目標 90%)。

## 還沒做

- 通膨(目前不納入;報告需寫明假設)
- 開放式問答(只做參數調整型的自由追問)

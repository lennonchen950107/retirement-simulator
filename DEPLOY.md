# 部署成網站(Streamlit Community Cloud)

目標:讓評審用一個網址就能開啟 app。金鑰只放在平台的 Secrets,不放進程式碼或 GitHub。

## 一、把專案放上 GitHub
1. 註冊或登入 github.com,右上角 + → New repository。名稱例如 `retirement-simulator`,選 **Private**(Streamlit 免費版也能部署私人專案),不要勾選 README。
2. 進到新 repository 頁面,按 **uploading an existing file**。
3. 把解壓縮後 `retirement_simulator` **資料夾裡面的所有內容**(app.py、engine、data、prompts、tests、requirements.txt 等)拖進去,按 Commit changes。
   - 要看得到 `app.py` 在 repository 的最上層,不是在多一層資料夾裡。
   - **不要上傳**:`llm_usage.jsonl`、`parse_responses*.txt`、`followup_responses*.txt`、任何寫著金鑰的檔案。
   - 隱藏資料夾 `.streamlit` 與 `.gitignore` 不上傳也可以。

## 二、部署
1. 開 share.streamlit.io,用 GitHub 登入並授權。
2. Create app → 選剛才的 repository、branch 選 main、Main file path 填 `app.py`。
3. 展開 **Advanced settings**:
   - Python version 選 3.12 或 3.13。
   - Secrets 貼上下面兩行(把金鑰換成你自己的):
     ```
     LLM_API_KEY = "你的金鑰"
     LLM_MODEL = "gemini-3.5-flash-lite"
     ```
4. 按 Deploy,等幾分鐘。完成後會得到一個 `xxx.streamlit.app` 的網址。

## 三、部署後檢查(用手機也開一次)
- 表單試算、一句話描述、追問按鈕、自由追問都能用。
- 沒有出現「使用的是示範用假資料」的警告(代表真實資料有讀到)。
- 解說下方沒有出現「由程式模板產生」(代表 AI 有在運作)。

## 四、額度與風險
- 網址是公開的:任何拿到網址的人都會消耗你的金鑰額度。免費層額度用完時,AI 功能會顯示白話訊息並退回表單與程式模板,基本試算仍可用,不會壞掉。
- 錄影與 demo 前先自己測一次,確認額度還在;錄影前不要大量重複測試。
- 要避免被別人用光額度,可以把網站設成私人(只有受邀的帳號能開),或交作業時才分享網址。
- 如果金鑰曾經貼到不該貼的地方,到 AI Studio 的 API Keys 頁面刪掉並重建,再更新 Secrets。

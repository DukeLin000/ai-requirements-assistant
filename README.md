# 需求分析助手

這是一個本機執行的 Python POC。輸入需求文字後，可以檢視功能、待確認事項、驗收條件與測試案例，並下載 JSON 或 Markdown 草稿。所有草稿都需要人工審閱。

## 本機啟動

使用 Python 3.12。以下指令從專案根目錄執行。

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

**macOS 或 Linux**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

開啟 [本機需求分析頁](http://127.0.0.1:8000)。按「載入範例」並執行分析，可以看到時段人數上限如何連到驗收條件，以及取消期限如何留為待確認。

## 分析與匯出

- Mock 模式以固定規則擷取候選需求。預約範例示範完整的 Given／When／Then 與測試案例，其餘輸入以擷取片段和待確認事項為主。啟動只需本機環境，無需 API 金鑰。
- 後端檢查資料結構、原文引用、編號關聯與案例上限。仍需確認業務規則的測試案例會標出阻塞事項，預期結果保留為 JSON 的 null。來源文字存在只證明引用位置可核對，語意由人員審閱。
- 瀏覽器用同一份已驗證的結果產生兩種下載檔。JSON 另外附上輸入的系統名稱與草稿提示。Markdown 也保留待確認狀態及引用。輸入與結果在目前頁面中處理，下載後由使用者管理。

## BA 審閱與交接

1. 貼上需求書文字並執行分析。從分析草稿選「前往 BA 審閱」，填寫既有需求單號、來源文件與版次，再補上業務目標與範圍。
2. 編輯或新增業務規則。系統保留擷取時的原文引用，可另填文件段落或表格位置。標記規則為「業務已確認」時，填寫確認依據。對待確認問題記錄決議、確認人與日期。
3. 以 Given／When／Then 編輯驗收條件，關聯業務規則並選取會影響該條件的問題。規則已確認、關聯問題都有完整決議，且驗收條件填寫完整時，該項會顯示「可供驗收設計」。其餘項目保留待確認或待補狀態。
4. 選「下載 BA 交接稿」，把 Markdown 附回原需求單供 SA 接手。匯出保留規則編號、問題決議、驗收狀態與原文引用，並保留原本的分析匯出供核對。

BA 審閱內容存於目前頁面的記憶體。重新分析會開一份新草稿，關閉或重整頁面會清除編輯內容。請先下載交接稿留存。

## API

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | / | 分析頁面 |
| GET | /healthz | 程序存活檢查 |
| GET | /openapi.json | API 契約 |
| POST | /api/v1/analyses | 分析需求並回傳草稿 |

分析端點接受 application/json，包含必要的 requirement_text、選填的 system_name 與 max_test_cases。需求換行正規化並去除首尾空白後須為 20 至 10,000 個 Unicode 字元。案例上限預設 8，接受 1 至 12 的整數。

每次回應附請求編號。分析回應使用 Cache-Control 的 no-store。服務限制為單一程序同時一筆分析，忙碌時回傳 429。UI 及靜態資源同站提供，使用自製或已去識別的需求。日誌只記錄請求編號、狀態、耗時與筆數等資料。

## 測試

使用專案的 Python 環境安裝 requirements-dev.txt，然後執行下列指令。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

測試涵蓋預約範例、一般文字、輸入邊界、分段傳輸的大小限制、Host 與 Origin、來源檢核、阻塞案例，以及併發與逾時後的名額釋放。

BA 草稿的關聯、決議狀態和匯出測試使用 Node.js 20 或以上版本執行。

```bash
node --test tests/ba-draft.test.mjs
```

## 後續里程碑

分析服務以 Analyzer 介面接收模型輸出，讓核准的行內模型沿用現有資料與來源檢核。行內版本再加入 Word 需求書匯入、唯讀 SVN、需求單跨階段追溯、SA Word、QA Excel 與實測紀錄。

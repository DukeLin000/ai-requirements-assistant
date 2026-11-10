# 需求分析助手

這是一個本機執行的 Python POC。BA 可登錄多筆需求、核對規則、設定等級，並按功能範圍產出有版次的 BA Word 文件。開發團隊可在同一工作台記錄 SA 接單、退回及 PG 接手。

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

開啟 [本機需求工作台](http://127.0.0.1:8000)。更新現有環境時先重新安裝 requirements.txt 再重啟服務。

## 多筆需求工作台

1. 在「BA 需求清單」新增需求單，登錄需求文件或公文的編號與版次，再貼上本筆要分析的原文。可選附一份 2 MiB 以內的 PDF、DOCX 或文字檔供下載核對。同一來源類型、編號與版次可供多筆需求共用。
2. 開啟需求並執行分析。BA 在一張規則卡核對原文、問題及驗收條件，按「儲存 BA 審閱」後可重開需求繼續工作。
3. 在「分級、文件與派發」輸入公司使用的等級，調整功能名稱與關聯規則，再勾選本次範圍。選擇需要內嵌的 UML 圖後，可預覽或產出 BA Word。文件採通用版型，包含來源、決議、驗收及所選功能。
4. 選「派發給 SA」並填承接團隊。所選功能須有已確認的規則及完整驗收條件。與所選驗收關聯的未決問題會阻塞派發，其他問題可以留在草稿。
5. 在「開發團隊接單」下載當次 BA Word，記錄 SA 接單或退回原因。SA 整理規格後登錄規格編號或位置，再交給 PG。退回的功能可由 BA 補充並以新文件版次重派。

需求、審閱、來源附件與文件版次保存在專案的 .data/requirements.sqlite3。可用 REQUIREMENTS_DB_PATH 指定其他本機 SQLite 檔案。每次文件產出保存當次的 Word 檔，SA 依派發版次下載。工作台提供本機流程驗證，BA、SA 與 PG 的操作使用同一個本機介面。

## 分析與匯出

- Mock 模式以固定規則擷取候選需求。預約範例示範完整的 Given／When／Then 與測試案例，其餘輸入以擷取片段和待確認事項為主。啟動只需本機環境，無需 API 金鑰。
- 後端檢查資料結構、原文引用、編號關聯與案例上限。仍需確認業務規則的測試案例會標出阻塞事項，預期結果保留為 JSON 的 null。來源文字存在只證明引用位置可核對，語意由人員審閱。
- 瀏覽器用同一份已驗證的結果產生兩種下載檔。JSON 另外附上輸入的系統名稱與草稿提示。Markdown 也保留待確認狀態及引用。輸入與結果在目前頁面中處理，下載後由使用者管理。

## BA 審閱與交接

1. 從分析草稿選「前往 BA 審閱」，補上業務目標與範圍。工作台需求會帶入來源資料。自由分析時請填寫需求單號、來源文件與版次。
2. 編輯或新增業務規則。系統保留擷取時的原文引用，可另填文件段落或表格位置。標記規則為「業務已確認」時，填寫確認依據。對待確認問題記錄決議、確認人與日期。
3. 以 Given／When／Then 編輯驗收條件，關聯業務規則並選取會影響該條件的問題。規則已確認、關聯問題都有完整決議，且驗收條件填寫完整時，該項會顯示「可供驗收設計」。其餘項目保留待確認或待補狀態。
4. 選「下載 BA Markdown」，可將規則編號、問題決議、驗收狀態與原文引用附回既有工單。

自由分析的 BA 草稿暫存於目前頁面。可按「載入範例」練習預約需求，下載交接稿留存。

## UML 活動圖

1. 執行分析後選「檢視 UML 活動圖」。選取業務規則或已關聯規則的驗收情境，圖上保留 `BR`、`Q` 或 `AC` 編號供核對。
2. Mock 可依明確的「先…，再…」語句，或「查詢並下載報表，格式為 Excel／CSV／PDF」的文字產出流程建議。其他規則以候選內容與待補提示起稿，可用 BA 驗收欄位補上前置條件、操作和預期結果。
3. 以分行報表為例，查無資料的分支會保留「待業務確認」。填齊該問題的決議、確認人與日期後，圖上顯示決議內容。編輯原規則後，該規則的流程會回到待補狀態，供 BA 依新規則整理驗收情境。
4. 填妥需求單號、來源文件及版次後，可下載 SVG 圖與 PlantUML 原始碼。BA Markdown 交接稿附上目前選取的圖面原始碼，並列出對應的 SVG 檔名。圖面在本機頁面產生。

## API

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | / | 分析頁面 |
| GET | /healthz | 程序存活檢查 |
| GET | /openapi.json | API 契約 |
| POST | /api/v1/analyses | 分析需求並回傳草稿 |
| GET / POST | /api/v1/requirements | 需求清單與登錄 |
| GET | /api/v1/requirements/{number} | 需求及審閱明細 |
| POST | /api/v1/requirements/{number}/analysis | 分析已登錄的需求 |
| PUT | /api/v1/requirements/{number}/review | 儲存分級、功能與審閱 |
| POST | /api/v1/requirements/{number}/preview | 預覽選定範圍的 BA 文件 |
| POST | /api/v1/requirements/{number}/documents | 保存 BA Word 草稿版次 |
| GET | /api/v1/requirements/{number}/documents/{version} | 下載指定 Word 版次 |
| POST | /api/v1/requirements/{number}/dispatches | 派發並保存當次 Word |
| GET | /api/v1/dispatches | 開發團隊接單清單 |
| POST | /api/v1/requirements/{number}/dispatches/{version}/transition | SA 或 PG 更新接單狀態 |

分析端點接受 application/json，包含必要的 requirement_text、選填的 system_name 與 max_test_cases。需求換行正規化並去除首尾空白後須為 20 至 10,000 個 Unicode 字元。案例上限預設 8，接受 1 至 12 的整數。

功能的 `activity` 欄位包含有序動作與選填的條件分支。分支引用須連到同一功能的待確認問題。待補流程的功能以 `null` 表示。

每次回應附請求編號。分析回應使用 Cache-Control 的 no-store。服務限制為單一程序同時一筆分析，忙碌時回傳 429。UI 及靜態資源同站提供，使用自製或已去識別的需求。日誌只記錄請求編號、狀態、耗時與筆數等資料。

## 測試

使用專案的 Python 環境安裝 requirements-dev.txt，然後執行下列指令。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

測試涵蓋來源檢核、共用附件、規則阻塞、部分派發、Word 版次、退回與重派、併發版本衝突，以及輸入邊界。

BA 草稿、活動圖的關聯、決議狀態和匯出測試使用 Node.js 20 或以上版本執行。

```bash
node --test tests/ba-draft.test.mjs tests/ba-activity.test.mjs
```

## 後續里程碑

分析服務以 Analyzer 介面接收模型輸出，讓核准的行內模型沿用現有資料與來源檢核。行內版本可接續公司 BA 範本、多人帳號與權限、Word 原文匯入、唯讀 SVN、SA Word 與 QA Excel。

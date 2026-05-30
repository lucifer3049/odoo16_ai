# Odoo 16 台股投資 AI 助理

一個建構在 Odoo 16 上的台股投資 AI 助理：整合**台灣證交所每日資料**、**多家 AI 供應商可逐題切換**、**pgvector 向量知識庫（API 資料＋使用者上傳檔案）**，並以**投資策略專家**角色提供分析與建議。

> 📌 本檔案是專案的單一事實來源（single source of truth）。**每次規劃或升級功能都會同步更新**「功能總覽」與最末的「變更紀錄」。

---

## 目錄
- [功能總覽](#功能總覽)
- [系統架構](#系統架構)
- [目錄結構](#目錄結構)
- [安裝與啟動](#安裝與啟動)
- [一鍵升級](#一鍵升級)
- [開發環境（Dev Container）](#開發環境dev-container解決-import-警告)
- [AI 供應商與模型](#ai-供應商與模型)
- [知識庫（RAG）](#知識庫rag)
- [API 端點](#api-端點)
- [資料庫維護](#資料庫維護)
- [分支策略與 CI/CD](#分支策略與-cicd)
- [未來可延伸功能 / AI Agent 應用](#未來可延伸功能--ai-agent-應用)
- [變更紀錄](#變更紀錄)
- [常見問題](#常見問題)

---

## 功能總覽

### 🤖 多供應商 LLM（逐題自由切換）
- 支援 **OpenAI / Google Gemini / Anthropic Claude / Groq / Ollama** 五家。
- **每一題都能獨立選擇**要用哪家、哪個模型（像 ChatGPT/Claude 的模型下拉），切換不影響其他對話。
- 五家 API 金鑰可**同時保存**在「AI 設定」，切換時無痛、不用重填。
- 聊天介面顯示「目前模型」，每則回覆也標註是哪個模型回答；未設金鑰的供應商會標示。
- 三家後端統一 `max_tokens=2048`、歷史只帶最近 2 輪 → 省 token、控成本。

### 📋 資料驅動的模型清單（免改程式）
- 模型清單存在 `ai.model` 目錄，使用者可在「**AI 模型清單**」選單**自行增刪改、設預設、排序、停用**。
- 想對齊市面任何新模型，只要新增一筆「模型代碼 = 廠商 API 的 model id」即可，**IT 不需改程式碼**。
- 供應商仍為固定清單（每家需對應後端實作），模型則完全資料化。

### 📈 每日台股資料 → 向量知識庫（排程）
- `ir.cron` 每日 **台灣時間 18:00**（UTC 10:00）自動執行。
- 抓 TWSE 全市場個股當日 OHLCV ＋月均價＋產業別＋大盤摘要。
- 每檔個股組成中文摘要文件、批次嵌入，寫入 **pgvector** 向量庫。
- 保留天數可設定（`ir.config_parameter` → `odoo_ai_assistant.digest_retention_days`，預設 10 個交易日），自動清理舊快照。

### 📂 使用者上傳檔案 → 同一向量庫（RAG ingestion）
- 「**知識庫匯入**」上傳檔案，自動解析 → 切塊 → 嵌入 → 進向量庫。
- 支援格式：**PDF、Word(.docx)、Excel(.xlsx)、CSV、純文字、Markdown**。
- **掃描型 PDF 自動 OCR**（pypdf 抽不到文字時，PyMuPDF 渲染＋Tesseract 中英文辨識）。
- 切塊長度／重疊**逐檔可調**（預設 600 字 / 重疊 100，於邊界斷句）。
- 「整批重新索引所有來源」一鍵重建；刪除來源會連帶刪除其 chunks。
- API 每日資料與上傳檔案**共用同一個 pgvector 檢索**，LLM 一次查詢同時看到兩者。

### 🧠 投資策略專家 + 自動上下文
- System Prompt 為「台股投資策略與建議專家」：技術面／基本面／籌碼面／風險控管，輸出採「觀點→依據→策略→風險」並附免責聲明。
- 可在「AI 設定」自訂覆寫 System Prompt。
- 對話**自動注入**當日大盤摘要＋RAG 檢索到的相關文件當上下文（預設開啟 RAG）。

### 🔧 Function Calling 工具
- 即時個股行情、個股日成交、股票搜尋、大盤指數，跨五家供應商皆可用。

### 💬 提問管理頁
- 「**提問紀錄**」頁面可瀏覽/管理歷史問答：list＋form、搜尋（我的／今天／近 7 天／已完成／錯誤）、分組（狀態／模型／日期），可刪除舊紀錄。

### 🛠 維運自動化
- **`upgrade.ps1`**：一鍵升級＋重啟（自動偵測資料庫名）。
- **Dev Container**：編輯器直接用容器內 Python，解決 import 無法解析的警告。
- **孤兒欄位清除 migration**：移除欄位後自動清掉 PostgreSQL 殘留欄位，避免長期逼近每表 1600 欄上限。

---

## 系統架構

```
┌────────────────────────────────────────────────────────────────┐
│ 資料來源                                                          │
│   ① 每日 cron（TWSE open API）── 全市場快照                       │
│   ② 使用者上傳檔案（PDF/Word/Excel/CSV/txt/md，掃描檔走 OCR）     │
└───────────────┬──────────────────────────┬─────────────────────┘
                │ 解析/組文件               │ 解析/切塊
                ▼                          ▼
        ┌───────────────── 多語言 Embedding（本機）─────────────────┐
        │   paraphrase-multilingual-MiniLM-L12-v2（384 維）          │
        └───────────────────────────┬──────────────────────────────┘
                                     ▼
                    ┌──────── pgvector（ai_document.embedding_vec）────────┐
                    │   HNSW cosine 索引，O(log n) 檢索                     │
                    └───────────────────────────┬──────────────────────────┘
                                                 │ 檢索 top-k 當上下文
                                                 ▼
   使用者提問 ──► /ai/chat ──► LLMService（逐題選供應商/模型）──► 回答
                                  │
                                  └─ Function Calling（即時行情等工具）
```

---

## 目錄結構

```
Odoo16_LLM/
├── docker-compose.yml              # Odoo + pgvector/pgvector:pg15
├── Dockerfile                      # odoo:16 + AI 套件 + OCR(tesseract) + sentence-transformers
├── upgrade.ps1                     # ⭐ 一鍵升級 + 重啟
├── .devcontainer/devcontainer.json # VS Code Dev Container（容器內開發）
├── patch_urllib3.py
└── addons/odoo_ai_assistant/
    ├── __manifest__.py
    ├── data/
    │   ├── ir_cron.xml             # 每日 18:00 台股向量庫排程
    │   └── ai_model_data.xml       # 預設模型種子（noupdate，使用者可改）
    ├── migrations/
    │   └── 16.0.1.1.0/
    │       └── post-migration.py   # 孤兒欄位清除（明確清單）
    ├── models/
    │   ├── ai_settings.py          # 多家金鑰庫 + 預設供應商 + 取 cfg
    │   ├── ai_chat.py              # 對話紀錄
    │   ├── ai_document.py          # RAG 文件 + pgvector 欄位/索引 + 每日 cron
    │   ├── knowledge_source.py     # 上傳檔案來源（解析/切塊/索引）
    │   ├── llm_model.py            # ai.model 模型目錄（使用者維護）
    │   ├── stock_quote.py
    │   └── stock_watchlist.py
    ├── services/
    │   ├── llm_service.py          # 五家後端 + tool-calling loop
    │   ├── tool_service.py         # Function Calling 工具
    │   ├── twse_service.py         # TWSE API（含全市場快照）
    │   ├── market_digest_service.py# 全市場快照 → 每日中文文件
    │   ├── document_loader_service.py # 檔案抽文字（含 OCR）+ 切塊
    │   ├── prompt_service.py       # 投資策略專家 prompt
    │   └── embedding_service.py    # 多語言 embedding + pgvector 檢索
    ├── controllers/
    │   └── main.py                 # /ai/chat、/ai/config、/ai/watchlist…
    ├── static/src/                 # Owl 聊天介面（含模型切換 UI）
    └── views/                      # 後台介面與選單
```

---

## 安裝與啟動

### 環境需求
- Docker Desktop / Docker Engine + `docker compose` v2
- （選用）本機 [Ollama](https://ollama.com/download)

### 第一次啟動
```powershell
cd d:\Odoo16_LLM
docker compose up -d --build       # 建 image（含 pgvector、OCR、sentence-transformers）並啟動

# 開瀏覽器 http://localhost:8069 建立資料庫，Apps → 搜尋「Taiwan Stock AI」→ Install
```
> 首次使用 RAG 會自動下載多語言 embedding 模型（約 470MB），容器需可連網一次。

### 日常啟動 / 停止
```powershell
docker compose up -d        # 啟動
docker compose down         # 停止
docker compose ps           # 狀態
docker compose logs -f web  # 即時 log
docker compose exec -u root web pip install # 安裝套件
```

---

## 一鍵升級

改完程式後，**不用再背指令**：
```powershell
.\upgrade.ps1
```
自動：啟動容器 → 偵測資料庫名 → `odoo -u odoo_ai_assistant --stop-after-init`（含 migration）→ 重啟 web。

| 情境 | 指令 |
|---|---|
| 一般升級（改 Python/XML） | `.\upgrade.ps1` |
| 改過 Dockerfile（OCR、pgvector 等依賴） | `.\upgrade.ps1 -Build` |
| 多個資料庫需指定 | `.\upgrade.ps1 -Db 你的DB名` |
| 第一次被執行原則擋住 | `powershell -ExecutionPolicy Bypass -File .\upgrade.ps1` |

---

## 開發環境（Dev Container，解決 import 警告）

編輯器（VS Code Pylance）的「Import 無法解析」黃色警告，是因為 AI/Odoo 套件都裝在 **Docker 容器內的 Python 3.9**，而 VS Code 預設用**本機 Python** 分析，找不到套件 —— 程式執行其實正常。

本專案已附 [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json)，讓 VS Code 直接在容器裡開發：

1. 安裝 VS Code 擴充套件 **Dev Containers**（`ms-vscode-remote.remote-containers`）。
2. 開啟專案 → 命令面板（F1）→ **Dev Containers: Reopen in Container**。
3. 等容器啟動後，編輯器即改用容器內 Python 3.9，所有 import（含 `odoo`）都能解析、自動補全、跳定義。

> 工作區會切到容器內的 `/mnt/extra-addons`（即 `addons/`，import 警告都在這）。要編輯 repo 根目錄檔案（README、Dockerfile…）時另開一般視窗即可。

---

## AI 供應商與模型

進入 **AI Assistant → AI 設定**：
- 一次把你有的幾家 API 金鑰全部填上、存檔（可同時並存）。
- `provider` 欄位只決定「預設供應商」；實際每題用哪家在聊天頁選。

進入 **AI Assistant → AI 模型清單**：新增/停用/設預設/排序你要出現在聊天下拉的模型。

| 供應商 | 申請 | 備註 |
|---|---|---|
| Groq | https://console.groq.com（免費） | 額度大，適合開發測試 |
| Ollama | 本機安裝 | 完全免費、無限制；模型名自由輸入 |
| OpenAI | https://platform.openai.com | |
| Gemini | https://aistudio.google.com | ⚠️ 免費層 `gemini-2.5-flash` 配額極緊、常立即 429，預設改用 `gemini-2.0-flash` |
| Claude | https://console.anthropic.com | |

> **Gemini 429 說明**：免費層對 `gemini-2.5-flash` 的配額極緊且有後端同步問題，換帳號也會立即 429。請用 `gemini-2.0-flash`／`gemini-2.5-flash-lite`，或開付費 Tier 1。

Ollama 模型範例：
```powershell
ollama pull qwen2.5     # 中文最佳（推薦）
ollama pull llama3.1    # 支援工具呼叫
```

---

## 知識庫（RAG）

### 來源一：每日台股（自動）
- 每日 18:00 自動更新；可手動觸發：**AI 設定 → 「立即更新今日向量庫」**。
- 保留天數：`ir.config_parameter` 的 `odoo_ai_assistant.digest_retention_days`（預設 10）。

### 來源二：上傳檔案（手動）
1. **AI Assistant → 知識庫匯入 → 新增**，上傳檔案。
2. 設定切塊長度／重疊（預設 600/100）。
3. 按「**解析並建立索引**」。掃描型 PDF 會自動 OCR。
4. 換 embedding 模型後可用 **AI 設定 → 「整批重新索引所有來源」/「重建手動文件向量」**。

---

## API 端點

| 端點 | 方法 | 說明 |
|---|---|---|
| `/ai/chat` | POST | 主要對話，支援 `use_tools`、`use_rag`、`provider`、`model`（逐題覆寫） |
| `/ai/config` | POST | 取得目前 provider/model 與各供應商可選模型清單、金鑰狀態 |
| `/ai/watchlist` | POST | 取得自選股 |
| `/ai/watchlist/add` | POST | 新增自選股 |
| `/ai/document/index` | POST | 對單一手動文件做 embedding |

---

## 資料庫維護

- **向量庫**：DB 使用 `pgvector/pgvector:pg15`；`ai_document.embedding_vec` 為原生 `vector(384)`，HNSW cosine 索引。
- **孤兒欄位清除**：移除模型欄位後，PostgreSQL 不會自動刪欄位（且每表上限 1600 欄，`DROP` 後仍占額度直到 table rewrite）。本專案以 `migrations/<version>/post-migration.py` 的**明確清單** `ORPHAN_COLUMNS` 自動清除。
  - **未來移除任何欄位**：① manifest `version` +1；② 把 `(表, 欄位)` 加進清單 → `.\upgrade.ps1` 時自動清。
  - ⚠️ 切勿用「掃描 `_fields` 外欄位就刪」的通用邏輯——會誤刪非 ORM 的 `embedding_vec`，毀掉 RAG。

```powershell
# 備份資料庫（將 odoo16 換成你的 DB 名）
docker compose exec db pg_dump -U odoo odoo16 > backup_$(Get-Date -Format 'yyyyMMdd').sql

# 完整清除重來（資料全刪）
docker compose down -v
docker compose up -d --build
```

---

## 分支策略與 CI/CD

採**簡化 GitFlow**：`main`（穩定/部署）← `develop`（整合）← `feature/*`；緊急修正走 `hotfix/*`。詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。

```
feature/* ──PR──► develop ──PR──► main ──tag v*──► CD 部署
```

- **CI**（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：每次 push / PR 自動跑
  1. Python 語法編譯
  2. 所有 XML 格式驗證
  3. **Odoo 模組安裝測試**（起 pgvector 服務 → 全新 DB 安裝模組 → 檢查無致命錯誤）
- **交付（GitHub 原生，推薦、零設定）**（[.github/workflows/release-ghcr.yml](.github/workflows/release-ghcr.yml)）：打 `v*` tag 時 build image → 推到 **GHCR**（`ghcr.io/lucifer3049/odoo16_ai`）→ 自動建立 GitHub Release。用內建 `GITHUB_TOKEN`，無需任何密鑰。
- **部署到 GCP（選用，上線時啟用）**（[.github/workflows/cd-gcp.yml](.github/workflows/cd-gcp.yml)）：打 `v*` tag 時 build → 推 GCP **Artifact Registry** → SSH 部署到 Compute Engine VM（pull＋升級＋重啟）。未設定 repo variable `GCP_PROJECT_ID` 前**自動跳過**。設定步驟見 [docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md)。

> GitHub 負責「測試＋打包 image」；「部署（把 image 跑起來）」需要一台運算服務（GCP VM 等）。起步可只用 GHCR，之後再接 GCP。

發佈：合併到 `main` → 打 tag（與 manifest 版本對齊）→ 自動部署。
```bash
git tag v16.0.1.2.0 && git push origin v16.0.1.2.0
```

---

## 未來可延伸功能 / AI Agent 應用

> 想法清單，依「近期實用 → 進階 Agent → 技術強化」分層；打勾代表已完成。

### A. 近期實用、好接的擴充
- [ ] **盤後自動報告**：收盤後自動產生「自選股／持股」分析，推播 Email / LINE Notify / Telegram。
- [ ] **價格與技術指標警示 Agent**：突破均線、爆量、創新高/低 → 主動通知。
- [ ] **更多資料工具**：三大法人買賣超、融資融券、外資持股、除權息、ETF 成分股。
- [ ] **新聞情緒 RAG**：定時爬個股新聞 → 向量庫 → 給 LLM 當情緒/事件面依據。
- [ ] **法說會/財報 PDF 自動匯入**：接上現有檔案 ingestion＋OCR，自動摘要重點。
- [ ] **回應串流（SSE）**：逐字輸出，改善等待體感。
- [ ] **RAG 引用標註**：回答標出處（來源檔名/日期），可信度更高。
- [ ] **上櫃（TPEX）支援**：目前以上市為主，補上櫃資料源。

### B. 進階 AI Agent 應用
- [ ] **多代理人辯論**：技術面 Agent × 基本面 Agent × 風險 Agent 各自分析後彙整結論，降低單一視角偏誤。
- [ ] **策略回測 Agent**：用自然語言描述策略 → 自動回測歷史資料 → 給績效與風險報告。
- [ ] **模擬投資組合（Paper Trading）**：對話式「買進/賣出」記錄虛擬部位，追蹤損益與風險暴露。
- [ ] **投資組合健檢 Agent**：定期檢視集中度、產業分散、相關性、回撤，提出再平衡建議。
- [ ] **目標導向理財 Agent**：依使用者風險屬性與目標，產生並滾動調整資產配置。
- [ ] **跨市場擴充**：美股、ETF、加密貨幣，統一同一套 RAG＋工具框架。
- [ ] **語音輸入/輸出**：語音問答，行動場景更順。

### C. 技術強化 / 平台化
- [ ] **多使用者化**：設定與金鑰改為 per-user，加上權限與用量隔離。
- [ ] **成本與用量儀表板**：記錄各供應商 token 用量與花費，找出最划算組合。
- [ ] **回答品質評估**：對 LLM 回覆做自動評分/標註，持續優化 prompt 與模型選擇。
- [ ] **長期記憶 / 使用者偏好 Profile**：記住個人關注標的、風險偏好、慣用語氣。
- [ ] **MCP / 外部工具整合**：以 Model Context Protocol 接更多資料源與行動。
- [ ] **pgvector 規模優化**：資料量大時調整索引參數、分區、或加 rerank 模型提升精準。
- [ ] **自動評測資料源健康度**：TWSE API 異常時告警與重試策略。

---

## 變更紀錄

> 格式：版本（日期）— 重點。每次升級在此新增一筆。

### v16.0.1.1.0（2026-05-30）— 向量庫升級與平台化
- **pgvector 向量庫**：`ai_document.embedding_vec` 原生 `vector(384)` + HNSW cosine 索引，取代純 Python cosine。
- **多語言 embedding**：改用 `paraphrase-multilingual-MiniLM-L12-v2`，中文檢索更準；`sentence-transformers` 已內建於 Dockerfile。
- **每日台股排程**：`ir.cron` 每日 18:00 抓全市場快照 → 每日中文文件 → 向量庫；保留天數可調。
- **投資策略專家 prompt**＋對話自動注入當日上下文（預設開 RAG）。
- **逐題切換供應商/模型**：每則訊息可獨立指定 provider/model，不寫全域；五家金鑰並存；聊天顯示目前模型與金鑰狀態。
- **資料驅動模型清單**：新增 `ai.model` 目錄與「AI 模型清單」選單，移除寫死的模型 Selection 欄位。
- **檔案匯入 RAG**：`ai.knowledge.source` 支援 PDF/Word/Excel/CSV/txt/md，切塊可調；**掃描型 PDF 自動 OCR**（tesseract）。
- **提問管理頁**：ai.chat 加 form/search/filter/group，可刪舊紀錄。
- **成本控制**：三後端 `max_tokens=2048`、歷史縮為 2 輪。
- **維運**：`upgrade.ps1` 一鍵升級＋重啟；孤兒欄位清除 migration（避免逼近 1600 欄上限）。
- **開發體驗**：新增 `.devcontainer` 讓 VS Code 在容器內開發，解決 import 無法解析；移除 docker-compose 過時的 `version` 屬性。

### v16.0.1.1.1（2026-05-30）— 工程化：GitFlow + CI/CD
- **簡化 GitFlow**：新增 `develop` 分支；`CONTRIBUTING.md` 規範分支流程；PR 模板。
- **CI**：GitHub Actions 自動跑 Python 語法、XML 驗證、Odoo 模組安裝測試（pgvector 服務）。
- **交付（GHCR）**：tag `v*` 觸發 build → 推 GitHub Container Registry + 建 Release（零設定）。
- **CD（GCP，選用）**：tag `v*` 觸發 build → Artifact Registry → 部署 Compute Engine VM；未設定 GCP 前自動跳過。
- **部署文件**：`docs/DEPLOY_GCP.md`（GCP 架構建議與設定步驟）、`docker-compose.prod.yml`。

### v16.0.1.0.0（2026-05-24）— 初版
- Odoo 16 模組：台股即時查詢、五家 AI 供應商、Function Calling、基本 RAG、Owl 聊天介面。

---

## 常見問題

**Q：編輯器一直顯示 import 無法解析？**
→ 套件裝在容器內、本機沒有。用 Dev Container（見「開發環境」）即可完整解析；程式執行本來就正常。

**Q：改了程式碼但 Odoo 沒反應？**
→ `.\upgrade.ps1`（或只改 Python 邏輯時 `docker compose restart web`）。

**Q：新增/移除欄位後畫面沒更新？**
→ 需升級模組：`.\upgrade.ps1`。

**Q：Gemini 一直 429？**
→ 免費層 `gemini-2.5-flash` 的問題，非你的帳號。改 `gemini-2.0-flash`／`gemini-2.5-flash-lite`，或用 Groq／Ollama，或開 Gemini 付費 Tier 1。

**Q：上傳的 PDF 抽不到內容？**
→ 若為掃描/圖片型 PDF，會自動走 OCR；請確認用 `-Build` 重建過 image（含 tesseract）。

**Q：Ollama 連不上？**
→ 確認 `ollama serve` 已啟動、模型已 `ollama pull`。

**Q：擔心資料庫欄位一直累積？**
→ 已有孤兒欄位清除機制；移除欄位時把它加進 migration 清單即可（見「資料庫維護」）。

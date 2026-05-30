# 部署到 GCP（建議與步驟）

本專案是**有狀態的 Odoo 服務**（filestore/sessions）＋ PostgreSQL(pgvector) ＋ sentence-transformers(CPU) ＋ Tesseract OCR。

## 建議架構

| 階段 | 架構 | 適用 |
|---|---|---|
| **起步（推薦）** | Compute Engine VM 跑 `docker-compose.prod.yml`，image 來自 Artifact Registry | 最快上線、最貼近本機 |
| **Production** | VM 跑 Odoo＋OCR；DB 換 **Cloud SQL for PostgreSQL（啟用 pgvector）**；filestore 放持久磁碟 | 要自動備份/HA |

> 不建議用 Cloud Run：Odoo 是長駐有狀態服務（filestore、session、longpolling），與 Cloud Run 的請求導向/短暫檔案系統/水平擴展不合。

建議規格：`e2-standard-2`（2 vCPU / 8GB），sentence-transformers 約需 2GB 記憶體。

---

## 一次性設定（gcloud）

```bash
# 變數
export PROJECT_ID=你的專案ID
export REGION=asia-east1
export ZONE=asia-east1-b
export AR_REPO=odoo
export INSTANCE=odoo-ai-vm

gcloud config set project $PROJECT_ID

# 1) 建 Artifact Registry（存 Docker image）
gcloud artifacts repositories create $AR_REPO \
  --repository-format=docker --location=$REGION

# 2) 建 VM（含 Docker，使用 Container-Optimized OS 或 Ubuntu）
gcloud compute instances create $INSTANCE \
  --zone=$ZONE --machine-type=e2-standard-2 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB --tags=http-server

# 開放 8069（或之後改用反向代理 + HTTPS）
gcloud compute firewall-rules create allow-odoo \
  --allow=tcp:8069 --target-tags=http-server

# 3) VM 上安裝 docker / docker compose、放 docker-compose.prod.yml 與 .env，
#    並 gcloud auth configure-docker $REGION-docker.pkg.dev
```

---

## GitHub Actions 部署認證（Workload Identity Federation，免金鑰，推薦）

```bash
# 服務帳號
gcloud iam service-accounts create gh-deployer

SA="gh-deployer@$PROJECT_ID.iam.gserviceaccount.com"
# 權限：推 image、SSH 到 VM
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/compute.instanceAdmin.v1"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/iam.serviceAccountUser"

# WIF pool / provider（綁定你的 GitHub repo）
gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository"
# 允許本 repo 取用該 SA（將 OWNER/REPO 換成 lucifer3049/odoo16_ai）
gcloud iam service-accounts add-iam-policy-binding $SA \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/attribute.repository/lucifer3049/odoo16_ai"
```

---

## 在 GitHub 設定 Variables / Secrets

**Settings → Secrets and variables → Actions**

Repository **variables**（明碼、控制 CD 是否啟用）：
| 名稱 | 範例 |
|---|---|
| `GCP_PROJECT_ID` | your-project-id |
| `GCP_REGION` | asia-east1 |
| `GCP_AR_REPO` | odoo |

Repository **secrets**（敏感）：
| 名稱 | 說明 |
|---|---|
| `GCP_WIF_PROVIDER` | `projects/<NUM>/locations/global/workloadIdentityPools/github/providers/github-provider` |
| `GCP_SA_EMAIL` | `gh-deployer@<PROJECT_ID>.iam.gserviceaccount.com` |
| `GCE_INSTANCE` | odoo-ai-vm |
| `GCE_ZONE` | asia-east1-b |
| `GCE_APP_DIR` | VM 上放 compose 的路徑，例 `/home/youruser/odoo16_ai` |

> 未填 `GCP_PROJECT_ID` 前，CD 工作流程會自動跳過，不影響 CI。

---

## 發佈流程

1. 合併到 `main` → 視為可發佈狀態。
2. 打版本 tag 觸發 CD：
   ```bash
   git tag v16.0.1.2.0
   git push origin v16.0.1.2.0
   ```
3. CD 會：build image → 推 Artifact Registry → SSH 到 VM `pull + 升級模組(含 migration) + 重啟`。

---

## 之後可強化
- 反向代理（Nginx / Caddy）+ Let's Encrypt HTTPS。
- DB 改 **Cloud SQL(pgvector)**，啟用自動備份與 PITR。
- filestore 改 GCS（odoo-gcs）或快照持久磁碟。
- 監控：Cloud Logging / Monitoring。

# 開發與分支規範

## 分支策略（簡化 GitFlow）

```
main      ← 穩定、可部署（打 tag 觸發 CD 部署）
 └ develop ← 整合分支（日常開發合流處）
     └ feature/<名稱>  ← 新功能/修改，從 develop 開、合回 develop
hotfix/<名稱>          ← 線上緊急修正，從 main 開、合回 main 與 develop
```

| 分支 | 從哪開 | 合併回 | 用途 |
|---|---|---|---|
| `main` | — | — | 穩定版；只接受 develop / hotfix 的 PR |
| `develop` | main | main | 整合所有功能 |
| `feature/*` | develop | develop | 單一功能或修改 |
| `hotfix/*` | main | main + develop | 線上緊急修正 |

## 日常流程

```bash
# 開新功能
git checkout develop && git pull
git checkout -b feature/my-feature
# ...開發... 
git push -u origin feature/my-feature
# 在 GitHub 開 PR：feature/my-feature → develop
```

- 累積一批功能後，開 PR `develop → main`。
- 合併到 main 後打版本 tag 觸發部署：
  ```bash
  git tag v16.0.1.2.0 && git push origin v16.0.1.2.0
  ```
- 版本號與 `addons/odoo_ai_assistant/__manifest__.py` 的 `version` 對齊。

## CI/CD

- **CI**（`.github/workflows/ci.yml`）：每次 push / PR 自動跑 Python 語法、XML 驗證、Odoo 模組安裝測試（pgvector）。
- **CD**（`.github/workflows/cd-gcp.yml`）：打 `v*` tag 時 build image → Artifact Registry → 部署 GCP VM；未設定 GCP 變數前自動跳過。詳見 [docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md)。

## 提交訊息
- 建議格式：`type: 簡短描述`（type：feat / fix / refactor / docs / chore）。

## 本機升級
改完程式後用一鍵腳本套用：`.\upgrade.ps1`（詳見 README）。

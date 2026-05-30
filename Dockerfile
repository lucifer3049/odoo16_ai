FROM odoo:16

USER root

COPY patch_urllib3.py /tmp/patch_urllib3.py
RUN python3 /tmp/patch_urllib3.py

# OCR 引擎與中文/英文語言包（掃描型 PDF 用）；git 供 vendor OCA 模組
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Vendor OCA queue_job（背景任務佇列）──────────────────────────────────────
# 不能用 pip install odoo-addon-queue-job：官方 image 以 .deb 裝 Odoo，pip 看不到，
# 會誤判要從 PyPI 拉整包 odoo 而爆掉。改用 git clone 把模組放進 /opt/oca，
# 該目錄不受 compose 的 ./addons bind mount 影響（見 odoo.conf 的 addons_path）。
# 註：為求可重現 build，驗證可運作後請把 --branch 16.0 改釘成特定 commit。
RUN git clone --depth 1 --branch 16.0 https://github.com/OCA/queue.git /tmp/oca-queue \
    && mkdir -p /opt/oca \
    && cp -r /tmp/oca-queue/queue_job /opt/oca/queue_job \
    && rm -rf /tmp/oca-queue

# 先裝 CPU-only 版 torch（本服務是 CPU 部署、無 GPU）。
# 若不指定，sentence-transformers 會拉進 CUDA 版 torch（數 GB 的 nvidia 庫），
# 在容器內 mmap 那些 .so 會因記憶體不足而 "failed to map segment"，導致 import 失敗。
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

# 其餘 Python 依賴一律由釘版本的 requirements.txt 安裝（可重現 build）。
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER odoo

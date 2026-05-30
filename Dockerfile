FROM odoo:16

USER root

COPY patch_urllib3.py /tmp/patch_urllib3.py
RUN python3 /tmp/patch_urllib3.py

# OCR 引擎與中文/英文語言包（掃描型 PDF 用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# 先裝 CPU-only 版 torch（本服務是 CPU 部署、無 GPU）。
# 若不指定，sentence-transformers 會拉進 CUDA 版 torch（數 GB 的 nvidia 庫），
# 在容器內 mmap 那些 .so 會因記憶體不足而 "failed to map segment"，導致 import 失敗。
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

# 其餘 Python 依賴一律由釘版本的 requirements.txt 安裝（可重現 build）。
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER odoo

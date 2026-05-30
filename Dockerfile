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

RUN pip install --no-cache-dir \
    "pyOpenSSL>=23.2.0" \
    openai \
    google-genai \
    anthropic \
    requests \
    sentence-transformers \
    pypdf \
    python-docx \
    pytesseract \
    pymupdf

USER odoo

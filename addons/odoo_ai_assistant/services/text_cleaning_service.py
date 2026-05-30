"""
TextCleaningService — RAG ingestion 的文字正規化／清洗（業界標準前處理）。

不論資料來自「外部上傳檔案」或「每日排程快照」，進向量庫前都應先過 clean_text，
確保同一段語意在不同來源下有一致的表示，提升檢索召回與 LLM 回答品質。

清洗步驟（順序有意義）：
  1. Unicode NFKC 正規化：全形→半形（`２３３０`→`2330`、全形空格`　`→` `）、
     相容字元統一。中文檢索最常見的雜訊就是全形/半形混用。
  2. 移除 BOM、零寬字元、軟連字號等不可見雜訊。
  3. 移除控制字元（保留 \n \t）。
  4. 空白正規化：行內連續空白收斂為一個、去除行尾空白、CRLF→LF、
     連續空行最多保留一個（維持段落感但不浪費 token）。
"""
import re
import unicodedata

# 零寬與不可見雜訊：零寬空格/連接符、軟連字號、BOM、方向標記
_INVISIBLE = re.compile(
    '[​‌‍⁠﻿­‎‏]'
)
# 控制字元（保留 \t \n），含 C0/C1 控制區
_CONTROL = re.compile(
    '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]'
)
_TRAILING_WS = re.compile(r'[ \t]+(?=\n)')      # 行尾空白
_INLINE_WS = re.compile(r'[ \t　]{2,}')      # 行內連續空白（含全形殘留）
_MANY_BLANK_LINES = re.compile(r'\n{3,}')        # 3+ 連續換行 → 2


def clean_text(text: str) -> str:
    """把任意來源文字正規化成乾淨、表示一致的純文字。"""
    if not text:
        return ''

    # 1. Unicode NFKC：全形→半形、相容字元統一
    text = unicodedata.normalize('NFKC', text)

    # 2. 移除零寬/不可見雜訊
    text = _INVISIBLE.sub('', text)

    # 3. 換行統一 + 移除控制字元
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _CONTROL.sub('', text)

    # 4. 空白正規化
    text = _TRAILING_WS.sub('', text)
    text = _INLINE_WS.sub(' ', text)
    text = _MANY_BLANK_LINES.sub('\n\n', text)

    return text.strip()

"""
EmbeddingService — 本機多語言句向量 + pgvector 檢索。

模型：paraphrase-multilingual-MiniLM-L12-v2（384 維，中文檢索佳）
向量存於 ai_document.embedding_vec（pgvector，由 ai.document.init 建立）
相似度檢索使用 pgvector cosine 運算子 `<=>`（HNSW 近似最近鄰）。

⚠️ 此模型 max_seq_length = 128 tokens：超過的部分會被「靜默截斷」而不報錯。
   因此切塊必須以模型 tokenizer 的 token 數為準（見 chunk()），不能用字元數盲切，
   否則每個 chunk 只有開頭 ~128 tokens 被向量化，後面內容檢索不到。
"""

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'

# 切塊目標 token 數：留 8 token 給特殊符號（[CLS]/[SEP] 等），避免觸發截斷
_CHUNK_HEADROOM = 8
# 相鄰 chunk 的 token 重疊，維持語意連續、避免答案剛好被切在邊界
_DEFAULT_OVERLAP_TOKENS = 20

# 句子邊界（中英文）：在這些符號後斷句，盡量讓 chunk 落在完整句子上
import re as _re
_SENTENCE_SPLIT = _re.compile(r'(?<=[。！？!?；;\n])')


class EmbeddingService:

    _model = None

    @classmethod
    def model_name(cls):
        return MODEL_NAME

    # ------------------------------------------------------------------
    # Tokenizer / token-aware 切塊（業界標準 RAG ingestion）
    # ------------------------------------------------------------------

    @classmethod
    def _tokenizer(cls):
        return cls._get_model().tokenizer

    @classmethod
    def max_tokens(cls):
        """模型可接受的最大 token 數（超過即截斷）。"""
        return int(cls._get_model().max_seq_length)

    @classmethod
    def _count_tokens(cls, text):
        return len(cls._tokenizer().encode(text, add_special_tokens=False))

    @classmethod
    def chunk(cls, text, target_tokens=None, overlap_tokens=_DEFAULT_OVERLAP_TOKENS):
        """
        以「模型 tokenizer 的 token 數」為界切塊，並盡量在句子邊界斷開。

        - target_tokens 預設 = max_tokens - headroom，確保每塊都在 128 內、不被截斷。
        - 單句若本身超長，退化為以 token 視窗硬切（仍保證不超界）。
        - 相鄰塊保留 overlap_tokens 重疊，避免關鍵句被切在邊界而檢索不到。
        回傳 list[str]（已過濾空塊）。
        """
        from .text_cleaning_service import clean_text

        text = clean_text(text)
        if not text:
            return []

        # 即使呼叫端傳入較大的值，也夾在模型上限內，杜絕「切了卻被截斷」
        ceiling = max(16, cls.max_tokens() - _CHUNK_HEADROOM)
        target_tokens = ceiling if target_tokens is None else min(int(target_tokens), ceiling)
        overlap_tokens = max(0, min(overlap_tokens, target_tokens // 2))

        tok = cls._tokenizer()
        sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]

        chunks = []
        cur, cur_tokens = [], 0

        def _flush():
            if cur:
                joined = ''.join(cur).strip()
                if joined:
                    chunks.append(joined)

        for sent in sentences:
            n = len(tok.encode(sent, add_special_tokens=False))

            # 單句就超界：先收掉目前塊，再對這句做 token 視窗硬切
            if n > target_tokens:
                _flush()
                cur, cur_tokens = [], 0
                ids = tok.encode(sent, add_special_tokens=False)
                step = target_tokens - overlap_tokens
                for i in range(0, len(ids), step):
                    piece = tok.decode(ids[i:i + target_tokens]).strip()
                    if piece:
                        chunks.append(piece)
                continue

            # 加上這句會超界：收掉目前塊，並用「尾端句子」建立重疊起點
            if cur_tokens + n > target_tokens and cur:
                _flush()
                carry, carry_tokens = [], 0
                for prev in reversed(cur):
                    pt = len(tok.encode(prev, add_special_tokens=False))
                    if carry_tokens + pt > overlap_tokens:
                        break
                    carry.insert(0, prev)
                    carry_tokens += pt
                cur, cur_tokens = carry, carry_tokens

            cur.append(sent)
            cur_tokens += n

        _flush()
        return chunks

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                # 保留原始錯誤：多數情況不是「沒安裝」，而是依賴鏈壞掉
                # （例如 torch 裝成 CUDA 版、Pillow<9.1），原始訊息才指得出真因。
                raise RuntimeError(
                    'RAG 功能無法載入 sentence-transformers（套件已裝於 Dockerfile，'
                    '若出錯通常是依賴鏈問題，如 torch/Pillow 版本）。'
                    '原始錯誤：%s' % e
                ) from e
            cls._model = SentenceTransformer(MODEL_NAME)
        return cls._model

    @classmethod
    def embed(cls, text):
        # 清洗是唯一進出口：查詢與文件都先正規化，確保同語意有一致向量表示
        from .text_cleaning_service import clean_text
        return cls._get_model().encode(clean_text(text)).tolist()

    @classmethod
    def embed_batch(cls, texts):
        """批次編碼，效率遠勝逐筆（每日上千檔個股的關鍵）。"""
        if not texts:
            return []
        from .text_cleaning_service import clean_text
        model = cls._get_model()
        cleaned = [clean_text(t) for t in texts]
        return model.encode(cleaned, batch_size=64).tolist()

    # ------------------------------------------------------------------
    # pgvector 相似度檢索
    # ------------------------------------------------------------------

    @classmethod
    def search_documents(cls, env, query, top_k=5, doc_types=None):
        """
        以 cosine 距離在 pgvector 上檢索最相關文件，回傳 ai.document recordset。
        doc_types: 限定文件類型，例如 ['manual', 'daily_stock', 'daily_market']
        """
        query_vec = cls.embed(query)
        vec_literal = '[' + ','.join(str(float(x)) for x in query_vec) + ']'

        sql = """
            SELECT id
            FROM ai_document
            WHERE active = TRUE
              AND embedding_vec IS NOT NULL
              AND embedding_model = %s
        """
        params = [MODEL_NAME]
        if doc_types:
            sql += " AND doc_type = ANY(%s)"
            params.append(list(doc_types))
        sql += " ORDER BY embedding_vec <=> %s::vector LIMIT %s"
        params += [vec_literal, top_k]

        env.cr.execute(sql, params)
        ids = [r[0] for r in env.cr.fetchall()]
        if not ids:
            return env['ai.document'].browse([])
        # 保留 SQL 的相似度排序
        docs = env['ai.document'].browse(ids)
        order = {doc_id: i for i, doc_id in enumerate(ids)}
        return docs.sorted(key=lambda d: order[d.id])

    @classmethod
    def latest_market_summary(cls, env):
        """取最新一筆每日大盤摘要文件（固定注入用）。"""
        return env['ai.document'].search(
            [('doc_type', '=', 'daily_market'), ('active', '=', True)],
            order='snapshot_date desc', limit=1,
        )

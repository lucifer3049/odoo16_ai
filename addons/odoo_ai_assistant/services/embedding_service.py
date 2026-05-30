"""
EmbeddingService — 本機多語言句向量 + pgvector 檢索。

模型：paraphrase-multilingual-MiniLM-L12-v2（384 維，中文檢索佳）
向量存於 ai_document.embedding_vec（pgvector，由 ai.document.init 建立）
相似度檢索使用 pgvector cosine 運算子 `<=>`（HNSW 近似最近鄰）。
需要：pip install sentence-transformers
"""

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'


class EmbeddingService:

    _model = None

    @classmethod
    def model_name(cls):
        return MODEL_NAME

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise RuntimeError(
                    'RAG 功能需要 sentence-transformers，'
                    '請在 Docker 容器內執行：pip install sentence-transformers'
                )
            cls._model = SentenceTransformer(MODEL_NAME)
        return cls._model

    @classmethod
    def embed(cls, text):
        return cls._get_model().encode(text).tolist()

    @classmethod
    def embed_batch(cls, texts):
        """批次編碼，效率遠勝逐筆（每日上千檔個股的關鍵）。"""
        if not texts:
            return []
        model = cls._get_model()
        return model.encode(list(texts), batch_size=64).tolist()

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

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# 與 EmbeddingService 使用的多語言模型維度一致
EMBED_DIM = 384


class AIDocument(models.Model):
    _name = 'ai.document'
    _description = 'AI RAG Document'
    _order = 'snapshot_date desc, id desc'

    name = fields.Char(required=True)
    content = fields.Text()
    active = fields.Boolean(default=True)

    doc_type = fields.Selection([
        ('manual',       '手動知識文件'),
        ('daily_stock',  '每日個股快照'),
        ('daily_market', '每日大盤摘要'),
    ], string='文件類型', default='manual', index=True)

    stock_no = fields.Char('股票代碼', index=True)
    snapshot_date = fields.Date('快照日期', index=True)
    embedding_model = fields.Char('嵌入模型', help='產生向量時使用的模型，換模型後需重建索引')
    source_id = fields.Many2one('ai.knowledge.source', string='來源檔案',
                                ondelete='cascade', index=True,
                                help='由上傳檔案解析切塊而來的 chunk；刪除來源時連帶刪除')

    _sql_constraints = [
        ('daily_stock_unique',
         'UNIQUE(doc_type, stock_no, snapshot_date)',
         '同一天同一檔個股只保留一筆快照'),
    ]

    # ------------------------------------------------------------------
    # pgvector 欄位 / 索引（ORM 不管理，以原生 SQL 建立）
    # ------------------------------------------------------------------

    def init(self):
        cr = self.env.cr
        cr.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_document' AND column_name = 'embedding_vec'
        """)
        if not cr.fetchone():
            cr.execute(
                "ALTER TABLE ai_document ADD COLUMN embedding_vec vector(%s)"
                % EMBED_DIM
            )
        # HNSW（cosine）近似最近鄰索引，撐全市場 × 多日資料
        cr.execute("""
            CREATE INDEX IF NOT EXISTS ai_document_embedding_vec_idx
            ON ai_document USING hnsw (embedding_vec vector_cosine_ops)
        """)

    # ------------------------------------------------------------------
    # 向量寫入
    # ------------------------------------------------------------------

    def _store_vector(self, vector):
        """把 list[float] 寫進 pgvector 欄位。"""
        self.ensure_one()
        vec_literal = '[' + ','.join(str(float(x)) for x in vector) + ']'
        self.env.cr.execute(
            "UPDATE ai_document SET embedding_vec = %s::vector WHERE id = %s",
            (vec_literal, self.id),
        )

    # ------------------------------------------------------------------
    # 每日排程：抓全市場快照 → 組文件 → 向量化 → 寫入 → 清理舊資料
    # ------------------------------------------------------------------

    @api.model
    def cron_build_daily_digest(self):
        """ir.cron 進入點：每日 18:00（台灣）執行。"""
        from ..services import market_digest_service
        from ..services.embedding_service import EmbeddingService

        retention_days = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'odoo_ai_assistant.digest_retention_days', '10'
            )
        )

        try:
            docs = market_digest_service.build_daily_documents()
        except Exception as e:
            _logger.exception('每日台股快照抓取失敗：%s', e)
            return

        if not docs:
            _logger.warning('每日台股快照無資料，略過。')
            return

        model_name = EmbeddingService.model_name()
        texts = [d['content'] for d in docs]
        vectors = EmbeddingService.embed_batch(texts)

        created = updated = 0
        for meta, vector in zip(docs, vectors):
            domain = [
                ('doc_type', '=', meta['doc_type']),
                ('snapshot_date', '=', meta['snapshot_date']),
            ]
            if meta.get('stock_no'):
                domain.append(('stock_no', '=', meta['stock_no']))
            existing = self.search(domain, limit=1)
            vals = {
                'name': meta['name'],
                'content': meta['content'],
                'doc_type': meta['doc_type'],
                'stock_no': meta.get('stock_no'),
                'snapshot_date': meta['snapshot_date'],
                'embedding_model': model_name,
                'active': True,
            }
            if existing:
                existing.write(vals)
                rec = existing
                updated += 1
            else:
                rec = self.create(vals)
                created += 1
            rec._store_vector(vector)

        # 清理保留天數以前的每日快照
        self._purge_old_snapshots(retention_days)

        self.env.cr.commit()
        _logger.info(
            '每日台股向量庫更新完成：新增 %s 筆、更新 %s 筆（保留 %s 天）。',
            created, updated, retention_days,
        )

    @api.model
    def _purge_old_snapshots(self, retention_days):
        from datetime import timedelta
        cutoff = fields.Date.context_today(self) - timedelta(days=retention_days)
        old = self.search([
            ('doc_type', 'in', ['daily_stock', 'daily_market']),
            ('snapshot_date', '<', cutoff),
        ])
        if old:
            old.unlink()

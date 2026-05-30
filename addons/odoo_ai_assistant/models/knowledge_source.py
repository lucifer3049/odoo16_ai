import base64
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIKnowledgeSource(models.Model):
    _name = 'ai.knowledge.source'
    _description = 'AI 知識庫來源檔案'
    _order = 'create_date desc'

    name = fields.Char('名稱', required=True)
    file = fields.Binary('檔案', attachment=True, required=True)
    file_name = fields.Char('檔名')
    description = fields.Text('說明')

    chunk_size = fields.Integer('切塊長度（字元）', default=600,
                                help='每個 chunk 的最大字元數')
    chunk_overlap = fields.Integer('重疊字元', default=100,
                                   help='相鄰 chunk 的重疊字元數，維持語意連續')

    status = fields.Selection([
        ('draft',   '待處理'),
        ('indexed', '已建立索引'),
        ('error',   '錯誤'),
    ], string='狀態', default='draft', readonly=True)
    chunk_count = fields.Integer('Chunk 數', readonly=True)
    error_message = fields.Text('錯誤訊息', readonly=True)

    chunk_ids = fields.One2many('ai.document', 'source_id', string='Chunks')

    # ------------------------------------------------------------------
    # 解析 → 切塊 → 嵌入 → 寫入向量庫
    # ------------------------------------------------------------------

    def action_index(self):
        from ..services import document_loader_service as loader
        from ..services.embedding_service import EmbeddingService

        for source in self:
            try:
                source._do_index(loader, EmbeddingService)
            except Exception as e:
                _logger.exception('知識庫來源解析失敗 [%s]：%s', source.name, e)
                source.write({'status': 'error', 'error_message': str(e)})
        return self._notify('索引處理完成。')

    def _do_index(self, loader, EmbeddingService):
        self.ensure_one()
        if not self.file:
            raise ValueError('尚未上傳檔案')

        data = base64.b64decode(self.file)
        text = loader.extract_text(data, self.file_name or self.name)
        chunks = loader.chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            raise ValueError('檔案解析後無可用文字內容')

        # 重新索引前先清掉舊 chunks
        self.chunk_ids.unlink()

        vectors = EmbeddingService.embed_batch(chunks)
        model_name = EmbeddingService.model_name()
        doc_model = self.env['ai.document']
        for i, (chunk, vector) in enumerate(zip(chunks, vectors), start=1):
            rec = doc_model.create({
                'name': f'{self.name} #{i}',
                'content': chunk,
                'doc_type': 'manual',
                'source_id': self.id,
                'embedding_model': model_name,
                'active': True,
            })
            rec._store_vector(vector)

        self.write({
            'status': 'indexed',
            'chunk_count': len(chunks),
            'error_message': False,
        })

    def action_reindex(self):
        return self.action_index()

    def _notify(self, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': 'success', 'sticky': False},
        }

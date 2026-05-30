from odoo import models, fields

# 各供應商在「目錄中找不到預設模型」時的最後保底值
_FALLBACK_MODEL = {
    'openai': 'gpt-4o-mini',
    'gemini': 'gemini-2.0-flash',
    'claude': 'claude-sonnet-4-6',
    'groq':   'llama-3.3-70b-versatile',
    'ollama': 'llama3.1',
}


class AISettings(models.Model):
    _name = 'ai.settings'
    _description = 'AI 設定'

    name = fields.Char('名稱', required=True, default='預設設定')
    active = fields.Boolean(default=True)

    provider = fields.Selection([
        ('openai',  'OpenAI'),
        ('gemini',  'Google Gemini'),
        ('claude',  'Anthropic Claude'),
        ('groq',    'Groq（免費雲端）'),
        ('ollama',  'Ollama（本機免費）'),
    ], string='預設供應商', default='gemini', required=True)

    # 各家金鑰（可同時設定，長期保存）
    openai_api_key = fields.Char('OpenAI API Key')
    gemini_api_key = fields.Char('Gemini API Key')
    claude_api_key = fields.Char('Claude API Key')
    groq_api_key = fields.Char('Groq API Key', help='至 console.groq.com 免費申請')

    # Ollama（本機，完全免費）
    ollama_host = fields.Char('Ollama Host', default='http://localhost:11434',
        help='Ollama 伺服器位址，預設 localhost:11434')

    temperature = fields.Float('Temperature', default=0.7)
    system_prompt = fields.Text('System Prompt 覆寫（留空使用預設）')

    def get_active_settings(self):
        settings = self.search([('active', '=', True)], limit=1)
        return settings or self

    def _default_model(self, provider):
        """從 ai.model 目錄取該供應商的預設模型代碼（is_default 優先，其次排序）。"""
        rec = self.env['ai.model'].search(
            [('provider', '=', provider), ('active', '=', True)],
            order='is_default desc, sequence, id', limit=1,
        )
        return rec.code or None

    def _resolve_model(self, provider, model):
        return model or self._default_model(provider) or _FALLBACK_MODEL.get(provider)

    def get_llm_config_for(self, provider, model=None):
        """
        以「指定供應商 + 指定模型」組出 cfg，金鑰一律取自本筆設定（多家並存）。
        model 留空則由 ai.model 目錄取該供應商預設。供每題獨立切換使用。
        """
        self.ensure_one()
        resolved = self._resolve_model(provider, model)
        if provider == 'openai':
            cfg = {'provider': 'openai', 'api_key': self.openai_api_key or '', 'model': resolved}
        elif provider == 'gemini':
            cfg = {'provider': 'gemini', 'api_key': self.gemini_api_key or '', 'model': resolved}
        elif provider == 'claude':
            cfg = {'provider': 'claude', 'api_key': self.claude_api_key or '', 'model': resolved}
        elif provider == 'groq':
            cfg = {
                'provider': 'groq', 'api_key': self.groq_api_key or '', 'model': resolved,
                'base_url': 'https://api.groq.com/openai/v1',
            }
        elif provider == 'ollama':
            host = (self.ollama_host or 'http://localhost:11434').rstrip('/')
            cfg = {
                'provider': 'ollama', 'api_key': 'ollama', 'model': resolved,
                'base_url': f'{host}/v1',
            }
        else:
            cfg = {'provider': 'gemini', 'api_key': self.gemini_api_key or '',
                   'model': resolved or _FALLBACK_MODEL['gemini']}

        cfg['system_prompt'] = (self.system_prompt or '').strip() or None
        cfg['temperature'] = self.temperature
        return cfg

    def get_llm_config(self):
        """回傳預設供應商的 cfg（未指定 per-message override 時使用）。"""
        return self.get_llm_config_for(self.provider)

    # ------------------------------------------------------------------
    # 手動操作按鈕
    # ------------------------------------------------------------------

    def action_rebuild_today_digest(self):
        """立即抓取今日台股並更新向量庫。"""
        self.env['ai.document'].cron_build_daily_digest()
        return self._notify('已更新今日台股向量庫。')

    def action_reindex_all_sources(self):
        """整批重新解析並索引所有上傳的知識庫來源檔案。"""
        sources = self.env['ai.knowledge.source'].search([])
        if sources:
            sources.action_index()
        return self._notify(f'已整批重新索引 {len(sources)} 份來源檔案。')

    def action_reindex_manual_docs(self):
        """換 embedding 模型後，重新為手動知識文件產生向量。"""
        from ..services.embedding_service import EmbeddingService
        docs = self.env['ai.document'].search([('doc_type', '=', 'manual')])
        model_name = EmbeddingService.model_name()
        for doc in docs:
            if not doc.content:
                continue
            doc._store_vector(EmbeddingService.embed(doc.content))
            doc.embedding_model = model_name
        return self._notify(f'已重建 {len(docs)} 筆手動文件索引。')

    def _notify(self, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': 'success', 'sticky': False},
        }

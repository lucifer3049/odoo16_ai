import re

from odoo import http
from odoo.http import request

from ..services.embedding_service import EmbeddingService

_STOCK_KEYWORDS = re.compile(
    r'\d{4,6}|股價|股票|行情|漲|跌|收盤|開盤|成交|大盤|指數|台積電|鴻海|聯發科|台塑|中鋼|國泰|富邦|玉山'
)


def _needs_tools(prompt: str) -> bool:
    return bool(_STOCK_KEYWORDS.search(prompt))


def _get_or_create_settings():
    Settings = request.env['ai.settings'].sudo()
    settings = Settings.get_active_settings()
    if not settings:
        settings = Settings.create({'name': '預設設定'})
    return settings


def _own_session(session_id):
    """取使用者自己的對話 session，找不到或非本人則回傳空 recordset。"""
    if not session_id:
        return request.env['ai.chat.session'].sudo().browse()
    session = request.env['ai.chat.session'].sudo().browse(session_id)
    if not session.exists() or session.user_id.id != request.env.user.id:
        return request.env['ai.chat.session'].sudo().browse()
    return session


class AIController(http.Controller):

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 模型設定（供前端顯示目前模型與切換）
    # ------------------------------------------------------------------

    @http.route('/ai/config', type='json', auth='user')
    def get_config(self):
        settings = _get_or_create_settings()
        Settings = request.env['ai.settings'].sudo()
        provider_selection = Settings.fields_get(['provider'])['provider']['selection']

        # 模型清單來自 ai.model 目錄（使用者自行維護）
        catalog = {}
        models_recs = request.env['ai.model'].sudo().search(
            [('active', '=', True)], order='provider, sequence, name')
        for rec in models_recs:
            catalog.setdefault(rec.provider, []).append({'value': rec.code, 'label': rec.name})

        key_present = {
            'openai': bool(settings.openai_api_key),
            'gemini': bool(settings.gemini_api_key),
            'claude': bool(settings.claude_api_key),
            'groq':   bool(settings.groq_api_key),
            'ollama': True,  # 本機免費，不需金鑰
        }
        providers = [
            {
                'value': pval, 'label': plabel,
                'models': catalog.get(pval, []),
                'has_key': key_present.get(pval, False),
            }
            for pval, plabel in provider_selection
        ]
        cfg = settings.get_llm_config()
        return {'provider': cfg['provider'], 'model': cfg['model'], 'providers': providers}

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    # provider → queue_job channel：雲端走可併發的 cloud，本機 Ollama 走 capacity=1 的 local_llm
    _CHANNEL_BY_PROVIDER = {
        'ollama': 'root.local_llm',
    }

    @http.route('/ai/chat', type='json', auth='user')
    def ai_chat(self, prompt, use_rag=True, use_tools=True, provider=None, model=None,
                session_id=None):
        """非同步化：建立 pending 紀錄並把 LLM 工作丟進 queue_job，立刻 return。
        實際回答由背景 job 透過 bus（頻道 ai_chat_<uid>）逐段推回前端。"""
        settings = _get_or_create_settings()
        # 每題可獨立指定 provider/model；未指定才用預設供應商
        cfg = settings.get_llm_config_for(provider, model) if provider else settings.get_llm_config()

        # 早期金鑰檢查：讓使用者立即得到回饋（job 內仍會再驗一次）
        if not cfg['api_key']:
            labels = {'openai': 'OpenAI', 'gemini': 'Gemini', 'claude': 'Claude', 'groq': 'Groq'}
            provider_label = labels.get(cfg['provider'], cfg['provider'])
            return {'error': f'請先在「AI 設定」中填入 {provider_label} API Key'}

        # 綁定對話 session：沿用前端帶來的，否則用第一句話開一個新對話
        Session = request.env['ai.chat.session'].sudo()
        session = _own_session(session_id)
        is_new_session = not session
        if not session:
            session = Session.create({
                'user_id': request.env.user.id,
                'name': Session.title_from_prompt(prompt),
            })

        # 建立 pending 訊息紀錄；session.touch 讓對話頂到最上
        record = request.env['ai.chat'].sudo().create({
            'session_id': session.id,
            'user_id': request.env.user.id,
            'prompt': prompt,
            'status': 'pending',
        })
        session.touch()

        # 依 provider 派送到對應 channel；job 內部再取 api_key（不經 job 參數落地）
        channel = self._CHANNEL_BY_PROVIDER.get(cfg['provider'], 'root.cloud')
        record.with_delay(
            channel=channel,
            description='AI chat #%s (%s)' % (record.id, cfg['provider']),
        ).process_chat(prompt, provider, model, use_rag, use_tools)

        return {
            'queued': True,
            'message_id': record.id,
            'provider': cfg['provider'],
            'model': cfg['model'],
            'session_id': session.id,
            'session_name': session.name,
            'is_new_session': is_new_session,
        }

    @http.route('/ai/chat/stop', type='json', auth='user')
    def ai_chat_stop(self, message_id):
        """使用者按「停止生成」：標記 cancel_requested，job 串流迴圈會偵測並中止。"""
        record = request.env['ai.chat'].sudo().browse(int(message_id))
        if record.exists() and record.user_id.id == request.env.user.id:
            record.cancel_requested = True
        return {'ok': True}

    # ------------------------------------------------------------------
    # 對話 session（ChatGPT 式側邊欄）
    # ------------------------------------------------------------------

    @http.route('/ai/sessions', type='json', auth='user')
    def list_sessions(self):
        """列出目前使用者的所有對話（最新活動排最前）。"""
        sessions = request.env['ai.chat.session'].sudo().search([
            ('user_id', '=', request.env.user.id),
        ])
        return [{'id': s.id, 'name': s.name} for s in sessions]

    @http.route('/ai/session/messages', type='json', auth='user')
    def session_messages(self, session_id):
        """載入某對話的完整訊息（user/assistant/error 依序展開）。"""
        session = _own_session(session_id)
        if not session:
            return {'messages': [], 'name': ''}
        messages = []
        for r in session.message_ids.sorted('id'):
            messages.append({'role': 'user', 'content': r.prompt})
            if r.status == 'done':
                messages.append({'role': 'assistant', 'content': r.response or '',
                                 'model': r.model_used})
            elif r.status == 'error':
                messages.append({'role': 'error', 'content': r.error_message or '（發生錯誤）'})
        return {'messages': messages, 'name': session.name}

    @http.route('/ai/session/delete', type='json', auth='user')
    def delete_session(self, session_id):
        """刪除某對話（連同其訊息，ondelete=cascade）。"""
        session = _own_session(session_id)
        if session:
            session.unlink()
        return {'ok': True}

    @http.route('/ai/session/rename', type='json', auth='user')
    def rename_session(self, session_id, name):
        """重新命名對話。"""
        session = _own_session(session_id)
        if session and (name or '').strip():
            session.name = name.strip()[:60]
        return {'ok': True, 'name': session.name if session else ''}

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    @http.route('/ai/watchlist', type='json', auth='user')
    def get_watchlist(self):
        items = request.env['stock.watchlist'].sudo().search([
            ('user_id', '=', request.env.user.id),
        ])
        return [
            {'stock_no': i.stock_no, 'name': i.name, 'note': i.note}
            for i in items
        ]

    @http.route('/ai/watchlist/add', type='json', auth='user')
    def add_to_watchlist(self, stock_no, name='', note=''):
        existing = request.env['stock.watchlist'].sudo().search([
            ('user_id', '=', request.env.user.id),
            ('stock_no', '=', stock_no),
        ], limit=1)
        if existing:
            return {'status': 'already_exists'}
        request.env['stock.watchlist'].sudo().create({
            'user_id': request.env.user.id,
            'stock_no': stock_no,
            'name': name,
            'note': note,
        })
        return {'status': 'added'}

    # ------------------------------------------------------------------
    # Document indexing (RAG)
    # ------------------------------------------------------------------

    @http.route('/ai/document/index', type='json', auth='user')
    def index_document(self, document_id):
        doc = request.env['ai.document'].sudo().browse(document_id)
        if not doc.exists():
            return {'error': 'Document not found'}
        vector = EmbeddingService.embed(doc.content or '')
        doc._store_vector(vector)
        doc.embedding_model = EmbeddingService.model_name()
        return {'status': 'indexed', 'document': doc.name}

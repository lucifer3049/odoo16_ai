import json
import re

from odoo import http
from odoo.http import request

from ..services.llm_service import LLMService
from ..services.tool_service import ToolService
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

    @http.route('/ai/chat', type='json', auth='user')
    def ai_chat(self, prompt, use_rag=True, use_tools=True, provider=None, model=None):
        settings = _get_or_create_settings()
        # 每題可獨立指定 provider/model；未指定才用預設供應商
        if provider:
            cfg = settings.get_llm_config_for(provider, model)
        else:
            cfg = settings.get_llm_config()

        if not cfg['api_key']:
            labels = {'openai': 'OpenAI', 'gemini': 'Gemini', 'claude': 'Claude', 'groq': 'Groq'}
            provider_label = labels.get(cfg['provider'], cfg['provider'])
            return {'error': f'請先在「AI 設定」中填入 {provider_label} API Key'}

        # 取最近 2 輪對話（省 token；過多歷史對精準度幫助有限）
        history_records = request.env['ai.chat'].sudo().search([
            ('user_id', '=', request.env.user.id),
            ('status', '=', 'done'),
        ], order='create_date desc', limit=2)
        history = [
            {'prompt': r.prompt, 'response': r.response}
            for r in reversed(history_records)
        ]

        try:
            # RAG 上下文：固定帶最新大盤摘要 + 依問題檢索相關每日/知識文件
            context_parts = []
            summary = EmbeddingService.latest_market_summary(request.env)
            if summary and summary.content:
                context_parts.append(summary.content)
            if use_rag:
                docs = EmbeddingService.search_documents(
                    request.env, prompt, top_k=5,
                    doc_types=['manual', 'daily_stock', 'daily_market'],
                )
                for d in docs:
                    if d.content and d.content not in context_parts:
                        context_parts.append(d.content)
            context = '\n\n'.join(context_parts) or None

            if use_tools and _needs_tools(prompt):
                final_text, tool_log = LLMService.chat_with_tools(
                    prompt,
                    tools=ToolService.get_tool_definitions(),
                    cfg=cfg,
                    env=request.env,
                    history=history,
                    context=context,
                )
                response_text = final_text or json.dumps(tool_log, ensure_ascii=False)
            else:
                response_text = LLMService.chat(
                    prompt,
                    cfg=cfg,
                    history=history,
                    context=context,
                )

            request.env['ai.chat'].sudo().create({
                'user_id': request.env.user.id,
                'prompt': prompt,
                'response': response_text,
                'model_used': cfg['model'],
                'status': 'done',
            })
            return {
                'response': response_text,
                'provider': cfg['provider'],
                'model': cfg['model'],
            }

        except Exception as e:
            # 取出最底層的原始例外，避免 RuntimeError 包裝層遮蔽真正原因
            root = e
            while root.__cause__ is not None:
                root = root.__cause__
            root_str = str(root)
            err_str = str(e)
            combined = f'{err_str} | root: {root_str}'

            if '429' in combined or 'quota' in combined.lower() or 'exhausted' in combined.lower():
                user_msg = (
                    '⚠️ Gemini API 回傳錯誤（可能是配額或權限問題）。\n'
                    f'Google 原始錯誤：{root_str[:300]}\n'
                    '請至 Google AI Studio 確認 API Key 與配額狀態。'
                )
            elif 'timed out' in combined.lower() or 'timeout' in combined.lower() or 'read operation' in combined.lower():
                user_msg = (
                    '⚠️ 請求逾時，AI 或行情 API 回應過慢。\n'
                    '請稍後再試，若持續發生請確認網路連線是否正常。\n'
                    f'（原始錯誤：{root_str[:200]}）'
                )
            else:
                user_msg = f'{err_str}\n（根因：{root_str[:300]}）' if root_str != err_str else err_str
            request.env['ai.chat'].sudo().create({
                'user_id': request.env.user.id,
                'prompt': prompt,
                'status': 'error',
                'error_message': err_str,
            })
            return {'error': user_msg}

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

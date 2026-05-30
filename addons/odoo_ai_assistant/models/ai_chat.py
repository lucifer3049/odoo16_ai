import json
import logging

import odoo
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# 串流時每累積到這個字數就推一次 bus（避免每個 token 都開獨立 cursor，太碎）
_FLUSH_EVERY_CHARS = 24


class AIChatSession(models.Model):
    _name = 'ai.chat.session'
    _description = 'AI Chat Session（對話）'
    # 以最後活動時間排序，最新的對話排最上面（ChatGPT 側邊欄行為）
    _order = 'last_activity desc, id desc'

    name = fields.Char(default='新對話')
    user_id = fields.Many2one('res.users', index=True,
                              default=lambda self: self.env.user, ondelete='cascade')
    message_ids = fields.One2many('ai.chat', 'session_id', string='訊息')
    # 由 controller 在每次新訊息後更新，用於排序，不依賴 write_date（加子紀錄不會動父層 write_date）
    last_activity = fields.Datetime(default=fields.Datetime.now, index=True)

    def touch(self):
        """每次有新訊息時呼叫，把對話頂到最上面。"""
        self.write({'last_activity': fields.Datetime.now()})

    @api.model
    def title_from_prompt(self, prompt):
        """用第一句話當對話標題（截斷）。"""
        text = (prompt or '').strip().replace('\n', ' ')
        return (text[:30] + '…') if len(text) > 30 else (text or '新對話')


class AIChat(models.Model):
    _name = 'ai.chat'
    _description = 'AI Chat History'
    _order = 'id'  # 同一對話內依建立順序（id 遞增）呈現

    session_id = fields.Many2one('ai.chat.session', string='所屬對話',
                                 ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    prompt = fields.Text(required=True)
    response = fields.Text()
    model_used = fields.Char()
    status = fields.Selection([
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='pending')
    error_message = fields.Text()
    # 使用者按「停止生成」時，stop endpoint 把它設 True；job 串流迴圈會偵測並中止。
    cancel_requested = fields.Boolean(default=False)

    # ------------------------------------------------------------------
    # 非同步處理（queue_job）+ bus 即時推送
    # ------------------------------------------------------------------

    @staticmethod
    def bus_channel(user_id):
        """每位使用者一條穩定 bus 頻道。前端在元件掛載時就訂閱（早於送出第一則訊息），
        避免「訂閱晚於推送」而漏接開頭 delta。每則 bus 訊息都帶 message_id，前端據此
        路由到正確的對話泡泡。
        註：字串頻道無伺服器端 ACL，理論上他人若自行 addChannel 同名可竊聽；本內部工具
        可接受，日後要硬化可改用 partner record 頻道或加隨機 token。"""
        return 'ai_chat_%s' % user_id

    def _bus_send(self, msg_type, payload):
        """用獨立短命 cursor 送一則 bus 通知並 commit，使其立即 NOTIFY 推出，
        而不動到正在執行的 job 主交易（保持 job 交易乾淨，交由 queue_job 收尾）。"""
        payload = dict(payload, message_id=self.id, type=msg_type)
        channel = self.bus_channel(self.user_id.id)
        dbname = self.env.cr.dbname
        try:
            with odoo.registry(dbname).cursor() as cr:
                env = api.Environment(cr, self.env.uid, {})
                env['bus.bus']._sendone(channel, 'ai_chat', payload)
                cr.commit()
        except Exception:
            # 推送失敗不該讓整個 job 崩；記 log 即可（前端最終仍會以 done/error 收斂）
            _logger.exception('ai.chat bus_send 失敗 (message_id=%s)', self.id)

    def _is_cancelled(self):
        """以獨立 cursor 重讀 cancel_requested（避開 job 交易快照看不到外部 commit）。"""
        try:
            with odoo.registry(self.env.cr.dbname).cursor() as cr:
                cr.execute('SELECT cancel_requested FROM ai_chat WHERE id = %s', (self.id,))
                row = cr.fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    def process_chat(self, prompt, provider, model, use_rag, use_tools):
        """queue_job 入口：執行 RAG → 工具 → 串流，邊產生邊用 bus 推 delta。
        本方法在獨立的 job 交易中執行；api_key 在此即時取得，不經 job 參數落地。"""
        self.ensure_one()
        from ..services.llm_service import LLMService
        from ..services.tool_service import ToolService
        from ..services.embedding_service import EmbeddingService

        Settings = self.env['ai.settings'].sudo()
        settings = Settings.get_active_settings() or Settings.create({'name': '預設設定'})
        cfg = settings.get_llm_config_for(provider, model) if provider else settings.get_llm_config()

        if not cfg.get('api_key'):
            labels = {'openai': 'OpenAI', 'gemini': 'Gemini', 'claude': 'Claude', 'groq': 'Groq'}
            label = labels.get(cfg['provider'], cfg['provider'])
            self._finish_error('請先在「AI 設定」中填入 %s API Key' % label)
            return

        try:
            self._bus_send('status', {'stage': 'retrieving', 'text': '檢索資料中…'})

            # 同一對話最近 2 輪歷史（與舊同步流程一致）
            history_records = self.env['ai.chat'].sudo().search([
                ('session_id', '=', self.session_id.id),
                ('status', '=', 'done'),
                ('id', '!=', self.id),
            ], order='id desc', limit=2)
            history = [{'prompt': r.prompt, 'response': r.response}
                       for r in reversed(history_records)]

            # RAG 上下文：固定帶最新大盤摘要 + 依問題檢索相關文件
            context_parts = []
            summary = EmbeddingService.latest_market_summary(self.env)
            if summary and summary.content:
                context_parts.append(summary.content)
            if use_rag:
                docs = EmbeddingService.search_documents(
                    self.env, prompt, top_k=5,
                    doc_types=['manual', 'daily_stock', 'daily_market'],
                )
                for d in docs:
                    if d.content and d.content not in context_parts:
                        context_parts.append(d.content)
            context = '\n\n'.join(context_parts) or None

            if self._is_cancelled():
                self._finish_cancelled('')
                return

            from ..controllers.main import _needs_tools
            if use_tools and _needs_tools(prompt):
                # 工具路徑：先非串流跑完工具迴圈（抓股價等），再把最終答案整段送出。
                self._bus_send('status', {'stage': 'tools', 'text': '查詢即時行情中…'})
                final_text, tool_log = LLMService.chat_with_tools(
                    prompt, tools=ToolService.get_tool_definitions(), cfg=cfg,
                    env=self.env, history=history, context=context,
                )
                full = final_text or json.dumps(tool_log, ensure_ascii=False)
                self._bus_send('delta', {'text': full})
            else:
                # 一般路徑：逐段串流，累積到一定字數就推一次。
                self._bus_send('status', {'stage': 'generating', 'text': 'AI 產生回答中…'})
                full = ''
                buffer = ''
                for piece in LLMService.chat_stream(prompt, cfg=cfg,
                                                    history=history, context=context):
                    full += piece
                    buffer += piece
                    if len(buffer) >= _FLUSH_EVERY_CHARS:
                        self._bus_send('delta', {'text': buffer})
                        buffer = ''
                        if self._is_cancelled():
                            self._finish_cancelled(full)
                            return
                if buffer:
                    self._bus_send('delta', {'text': buffer})

            self.write({
                'response': full,
                'model_used': cfg['model'],
                'status': 'done',
            })
            self.session_id.touch()
            self._bus_send('done', {'model': cfg['model']})

        except Exception as e:
            root = e
            while getattr(root, '__cause__', None) is not None:
                root = root.__cause__
            err_str = str(e)
            root_str = str(root)
            combined = ('%s | %s' % (err_str, root_str)).lower()
            if '429' in combined or 'quota' in combined or 'exhausted' in combined:
                user_msg = ('⚠️ %s\n——\n可先在上方切換到 OpenAI／Groq 繼續使用，'
                            '或至 https://aistudio.google.com 確認金鑰的專案配額。' % err_str)
            elif 'timed out' in combined or 'timeout' in combined or 'read operation' in combined:
                user_msg = ('⚠️ 請求逾時，AI 或行情 API 回應過慢，請稍後再試。\n（原始錯誤：%s）'
                            % root_str[:200])
            else:
                user_msg = ('%s\n（根因：%s）' % (err_str, root_str[:300])) \
                    if root_str != err_str else err_str
            self._finish_error(user_msg)

    def _finish_error(self, message):
        self.write({'status': 'error', 'error_message': message})
        self.session_id.touch()
        self._bus_send('error', {'text': message})

    def _finish_cancelled(self, partial):
        """使用者中止：保留已產生的部分文字，標記完成。"""
        self.write({
            'status': 'done',
            'response': (partial or '') + '\n\n（已由使用者停止生成）',
        })
        self.session_id.touch()
        self._bus_send('done', {'model': self.model_used or '', 'cancelled': True})

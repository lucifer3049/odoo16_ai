from odoo import models, fields, api


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

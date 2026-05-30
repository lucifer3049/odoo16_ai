from odoo import models, fields, api


class AIModel(models.Model):
    _name = 'ai.model'
    _description = 'AI 模型清單（使用者可自行維護）'
    _order = 'provider, sequence, name'

    name = fields.Char('顯示名稱', required=True,
                       help='聊天下拉顯示的名稱，例如 GPT-4.1 Mini')
    code = fields.Char('模型代碼', required=True,
                       help='傳給該廠商 API 的模型 ID，例如 gpt-4.1-mini')
    provider = fields.Selection([
        ('openai',  'OpenAI'),
        ('gemini',  'Google Gemini'),
        ('claude',  'Anthropic Claude'),
        ('groq',    'Groq'),
        ('ollama',  'Ollama（本機）'),
    ], string='供應商', required=True)
    sequence = fields.Integer('排序', default=10)
    is_default = fields.Boolean('預設模型',
                                help='未指定模型時，此供應商採用的預設；每家建議只設一個')
    active = fields.Boolean('啟用', default=True)

    _sql_constraints = [
        ('provider_code_unique', 'UNIQUE(provider, code)',
         '同一供應商的模型代碼不可重複'),
    ]

    def name_get(self):
        return [(rec.id, f'{rec.name} ({rec.code})') for rec in self]

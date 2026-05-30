from odoo import models, fields


class AIChat(models.Model):
    _name = 'ai.chat'
    _description = 'AI Chat History'
    _order = 'create_date desc'

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

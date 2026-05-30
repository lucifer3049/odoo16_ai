from odoo import models, fields


class StockWatchlist(models.Model):
    _name = 'stock.watchlist'
    _description = '台股自選股清單'
    _order = 'sequence, stock_no'

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, index=True)
    stock_no = fields.Char('股票代碼', required=True)
    name = fields.Char('股票名稱')
    note = fields.Text('備註')
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ('user_stock_unique', 'UNIQUE(user_id, stock_no)', '此股票已在自選清單中'),
    ]

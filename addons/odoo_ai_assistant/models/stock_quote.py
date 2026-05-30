from odoo import models, fields


class StockQuote(models.Model):
    _name = 'stock.quote'
    _description = '台股即時行情快取'
    _order = 'write_date desc'

    stock_no = fields.Char('股票代碼', required=True, index=True)
    name = fields.Char('股票名稱')
    market = fields.Selection([
        ('TWSE', '上市（TWSE）'),
        ('TPEX', '上櫃（TPEX）'),
    ], string='市場')
    price = fields.Char('最新成交價')
    open_price = fields.Char('開盤')
    high = fields.Char('最高')
    low = fields.Char('最低')
    yesterday_close = fields.Char('昨收')
    volume = fields.Char('成交量（張）')
    quote_time = fields.Char('報價時間')

    _sql_constraints = [
        ('stock_no_unique', 'UNIQUE(stock_no)', '每支股票只保留一筆快取'),
    ]

    def upsert_quote(self, data):
        existing = self.search([('stock_no', '=', data['stock_no'])], limit=1)
        vals = {
            'stock_no':        data.get('stock_no', ''),
            'name':            data.get('name', ''),
            'market':          data.get('market', 'TWSE'),
            'price':           data.get('price', ''),
            'open_price':      data.get('open', ''),
            'high':            data.get('high', ''),
            'low':             data.get('low', ''),
            'yesterday_close': data.get('yesterday_close', ''),
            'volume':          data.get('volume', ''),
            'quote_time':      data.get('time', ''),
        }
        if existing:
            existing.write(vals)
        else:
            self.create(vals)

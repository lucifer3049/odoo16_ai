{
    'name': 'Taiwan Stock AI Assistant',
    'version': '16.0.1.3.0',
    'summary': '台股投資 AI 助理：即時行情、AI 分析、知識問答',
    'license': 'LGPL-3',
    'depends': ['base', 'web', 'bus', 'queue_job'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/ai_model_data.xml',
        'views/ai_chat_views.xml',
        'views/stock_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'odoo_ai_assistant/static/src/css/chat_widget.css',
            'odoo_ai_assistant/static/src/xml/chat_widget.xml',
            'odoo_ai_assistant/static/src/js/chat_widget.js',
        ],
    },
    'installable': True,
    'application': True,
}

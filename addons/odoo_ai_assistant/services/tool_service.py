from . import twse_service
from datetime import datetime


class ToolService:

    TOOL_DEFINITIONS = [
        {
            'type': 'function',
            'function': {
                'name': 'get_realtime_quote',
                'description': '查詢台股個股即時行情，包含股價、成交量、漲跌等資訊',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'stock_no': {
                            'type': 'string',
                            'description': '股票代碼，例如 2330（台積電）、2317（鴻海）',
                        },
                    },
                    'required': ['stock_no'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_daily_history',
                'description': '查詢個股近期日成交資訊（開高低收、成交量），預設查當月',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'stock_no': {
                            'type': 'string',
                            'description': '股票代碼',
                        },
                        'date': {
                            'type': 'string',
                            'description': '查詢月份，格式 YYYYMMDD，留空預設當月',
                        },
                    },
                    'required': ['stock_no'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'search_stock',
                'description': '用股票代碼或公司名稱關鍵字搜尋台股',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'keyword': {
                            'type': 'string',
                            'description': '股票代碼或公司名稱，例如：台積電、2330',
                        },
                    },
                    'required': ['keyword'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_market_index',
                'description': '查詢台灣加權指數（大盤）即時資訊',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                },
            },
        },
    ]

    @staticmethod
    def get_tool_definitions():
        return ToolService.TOOL_DEFINITIONS

    @staticmethod
    def execute(env, tool_name, arguments):
        handlers = {
            'get_realtime_quote': ToolService._get_realtime_quote,
            'get_daily_history':  ToolService._get_daily_history,
            'search_stock':       ToolService._search_stock,
            'get_market_index':   ToolService._get_market_index,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {'error': f'未知的 tool：{tool_name}'}
        try:
            return handler(env, **arguments)
        except Exception as e:
            return {'error': str(e)}

    # -----------------------------------------------------------------------
    # Handlers（env 保留給未來 ORM 擴充，例如存 watchlist）
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_realtime_quote(env, stock_no):
        result = twse_service.get_realtime_quote(stock_no)
        # 順手快取一筆到 stock.quote
        if 'error' not in result and result.get('price', '-') != '-':
            try:
                env['stock.quote'].sudo().upsert_quote(result)
            except Exception:
                pass
        return result

    @staticmethod
    def _get_daily_history(env, stock_no, date=None):
        if not date:
            date = datetime.today().strftime('%Y%m%d')
        return twse_service.get_daily_history(stock_no, date)

    @staticmethod
    def _search_stock(env, keyword):
        return twse_service.search_stock(keyword)

    @staticmethod
    def _get_market_index(env):
        return twse_service.get_market_index()

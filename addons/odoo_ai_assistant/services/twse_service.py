"""
TWSE open data service — openapi.twse.com.tw (official REST API, no auth required).

Endpoints used:
  /v1/exchangeReport/STOCK_DAY_ALL     – all listed stocks, today's OHLCV
  /v1/exchangeReport/STOCK_DAY_AVG_ALL – all listed stocks, closing + monthly avg
  /v1/exchangeReport/MI_INDEX          – market indices (today)
  /v1/opendata/t187ap03_L              – listed company master list
"""

import time
import requests

_SESSION = requests.Session()
_SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (OdooAIAssistant/1.0)'})

TWSE_API = 'https://openapi.twse.com.tw/v1'

# In-process cache: url -> (data, timestamp)
_CACHE: dict = {}


def _get(url, timeout=15):
    try:
        resp = _SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        err = str(e)
        if 'timed out' in err.lower() or 'timeout' in err.lower():
            raise RuntimeError(f'台灣交易所 API 請求逾時（{timeout}s），請稍後再試。') from e
        raise


def _cached(url, ttl=60):
    now = time.time()
    if url in _CACHE:
        data, ts = _CACHE[url]
        if now - ts < ttl:
            return data
    data = _get(url)
    _CACHE[url] = (data, now)
    return data


# ---------------------------------------------------------------------------
# 即時行情（當日收盤 OHLCV）
# ---------------------------------------------------------------------------

def get_realtime_quote(stock_no: str) -> dict:
    """查詢上市個股當日行情。"""
    all_stocks = _cached(f'{TWSE_API}/exchangeReport/STOCK_DAY_ALL')

    for item in all_stocks:
        if item.get('Code') == stock_no:
            closing = item.get('ClosingPrice', '-')
            change_raw = item.get('Change', '0')
            # 計算昨收 = 收盤 - 漲跌
            try:
                yesterday_close = str(round(float(closing) - float(change_raw), 2))
            except (ValueError, TypeError):
                yesterday_close = '-'

            try:
                change_float = float(change_raw)
                direction = '▲' if change_float > 0 else ('▼' if change_float < 0 else '─')
            except (ValueError, TypeError):
                direction = ''

            return {
                'stock_no':       item.get('Code', stock_no),
                'name':           item.get('Name', ''),
                'price':          closing,
                'open':           item.get('OpeningPrice', '-'),
                'high':           item.get('HighestPrice', '-'),
                'low':            item.get('LowestPrice', '-'),
                'yesterday_close': yesterday_close,
                'change':         change_raw,
                'change_str':     f'{direction}{change_raw}',
                'volume':         item.get('TradeVolume', '-'),
                'time':           item.get('Date', ''),
                'market':         'TWSE',
            }

    return {'error': f'查無股票 {stock_no}（僅支援上市股票，上櫃股票尚未支援）'}


# ---------------------------------------------------------------------------
# 個股歷史 / 月均價
# ---------------------------------------------------------------------------

def get_daily_history(stock_no: str, _date: str = '') -> dict:
    """
    回傳個股當日交易摘要與月均價。
    date 參數保留相容性（openapi.twse.com.tw 不提供任意日期查詢，僅有當日資料）。
    """
    day_data = _cached(f'{TWSE_API}/exchangeReport/STOCK_DAY_ALL')
    avg_data = _cached(f'{TWSE_API}/exchangeReport/STOCK_DAY_AVG_ALL')

    stock_day = next((i for i in day_data if i.get('Code') == stock_no), None)
    stock_avg = next((i for i in avg_data if i.get('Code') == stock_no), None)

    if not stock_day and not stock_avg:
        return {'error': f'查無股票 {stock_no}'}

    base = stock_day or stock_avg
    result = {
        'stock_no':       stock_no,
        'name':           base.get('Name', ''),
        'date':           base.get('Date', ''),
        'monthly_avg':    (stock_avg or {}).get('MonthlyAveragePrice', '-'),
    }
    if stock_day:
        result.update({
            'open':    stock_day.get('OpeningPrice', '-'),
            'high':    stock_day.get('HighestPrice', '-'),
            'low':     stock_day.get('LowestPrice', '-'),
            'close':   stock_day.get('ClosingPrice', '-'),
            'change':  stock_day.get('Change', '-'),
            'volume':  stock_day.get('TradeVolume', '-'),
        })
    return result


# ---------------------------------------------------------------------------
# 股票搜尋（上市公司清單）
# ---------------------------------------------------------------------------

def search_stock(keyword: str) -> list:
    """用代碼或名稱關鍵字搜尋上市公司，回傳最多 10 筆。"""
    companies = _cached(f'{TWSE_API}/opendata/t187ap03_L', ttl=3600)

    kw = keyword.lower().strip()
    results = []
    for item in companies:
        code = item.get('公司代號', '')
        name = item.get('公司名稱', '') or item.get('公司簡稱', '')
        if kw in code.lower() or kw in name.lower():
            results.append({
                'stock_no': code,
                'name':     name,
                'market':   'TWSE',
                'industry': item.get('產業別', ''),
            })
            if len(results) >= 10:
                break
    return results if results else [{'note': f'查無符合「{keyword}」的上市公司'}]


# ---------------------------------------------------------------------------
# 大盤加權指數
# ---------------------------------------------------------------------------

def get_market_index() -> dict:
    """回傳台灣加權股價指數當日資訊。"""
    indices = _cached(f'{TWSE_API}/exchangeReport/MI_INDEX')

    for item in indices:
        if '加權股價指數' in item.get('指數', ''):
            change_dir = item.get('漲跌', '')
            change_pts = item.get('漲跌點數', '-')
            change_pct = item.get('漲跌百分比', '-')
            return {
                'name':       '台灣加權指數',
                'price':      item.get('收盤指數', '-'),
                'change':     f'{change_dir}{change_pts}',
                'change_pct': f'{change_pct}%',
                'date':       item.get('日期', ''),
                'market':     'TWSE',
            }

    return {'error': '無法取得大盤資訊'}


# ---------------------------------------------------------------------------
# 全市場每日快照（每日排程用）
# ---------------------------------------------------------------------------

def get_all_stocks_snapshot() -> dict:
    """
    抓全上市個股當日 OHLCV + 月均價 + 產業別，外加大盤摘要。
    回傳 {'date': 'YYYYMMDD', 'stocks': [...], 'market': {...}, 'breadth': {...}}。
    """
    day_data = _get(f'{TWSE_API}/exchangeReport/STOCK_DAY_ALL')
    avg_data = _get(f'{TWSE_API}/exchangeReport/STOCK_DAY_AVG_ALL')
    companies = _cached(f'{TWSE_API}/opendata/t187ap03_L', ttl=3600)

    avg_by_code = {i.get('Code'): i for i in avg_data}
    industry_by_code = {
        c.get('公司代號'): c.get('產業別', '')
        for c in companies
    }

    up = down = flat = 0
    stocks = []
    snapshot_date = ''
    for item in day_data:
        code = item.get('Code')
        if not code:
            continue
        snapshot_date = snapshot_date or item.get('Date', '')
        change_raw = item.get('Change', '0')
        try:
            change_f = float(change_raw)
            if change_f > 0:
                up += 1
            elif change_f < 0:
                down += 1
            else:
                flat += 1
        except (ValueError, TypeError):
            change_f = None

        stocks.append({
            'stock_no':    code,
            'name':        item.get('Name', ''),
            'open':        item.get('OpeningPrice', '-'),
            'high':        item.get('HighestPrice', '-'),
            'low':         item.get('LowestPrice', '-'),
            'close':       item.get('ClosingPrice', '-'),
            'change':      change_raw,
            'volume':      item.get('TradeVolume', '-'),
            'value':       item.get('TradeValue', '-'),
            'monthly_avg': avg_by_code.get(code, {}).get('MonthlyAveragePrice', '-'),
            'industry':    industry_by_code.get(code, ''),
        })

    return {
        'date':    snapshot_date,
        'stocks':  stocks,
        'market':  get_market_index(),
        'breadth': {'up': up, 'down': down, 'flat': flat, 'total': len(stocks)},
    }

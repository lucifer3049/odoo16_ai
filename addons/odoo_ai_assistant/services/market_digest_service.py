"""
MarketDigestService — 把 TWSE 全市場每日快照組成可向量化的中文文件。

build_daily_documents() 回傳 list[dict]，每筆含：
    {name, content, doc_type, stock_no, snapshot_date}
供 ai.document.cron_build_daily_digest 批次嵌入與寫入。
"""
from datetime import date, datetime

from . import twse_service


def _to_date(twse_date: str):
    """TWSE 的 YYYYMMDD 轉成 date；失敗則用今天。"""
    try:
        return datetime.strptime(twse_date, '%Y%m%d').date()
    except (ValueError, TypeError):
        return date.today()


def _pct(change, close):
    try:
        c = float(close)
        ch = float(change)
        prev = c - ch
        if prev:
            return f'{ch / prev * 100:+.2f}%'
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return '-'


def _stock_text(s, dstr):
    return (
        f"{s['name']}({s['stock_no']}) {dstr} 收盤 {s['close']} "
        f"漲跌 {s['change']}（{_pct(s['change'], s['close'])}） "
        f"開 {s['open']} 高 {s['high']} 低 {s['low']} "
        f"成交量 {s['volume']} 股 月均價 {s['monthly_avg']} "
        f"產業：{s['industry'] or '未分類'}"
    )


def _market_text(snap, dstr):
    m = snap.get('market') or {}
    b = snap.get('breadth') or {}
    return (
        f"台股大盤摘要 {dstr}：加權指數 {m.get('price', '-')}"
        f"（{m.get('change', '-')}，{m.get('change_pct', '-')}）。"
        f"上漲 {b.get('up', 0)} 家、下跌 {b.get('down', 0)} 家、"
        f"平盤 {b.get('flat', 0)} 家，共 {b.get('total', 0)} 檔上市個股。"
    )


def build_daily_documents():
    snap = twse_service.get_all_stocks_snapshot()
    stocks = snap.get('stocks') or []
    if not stocks:
        return []

    snap_date = _to_date(snap.get('date', ''))
    dstr = snap_date.strftime('%Y/%m/%d')

    docs = [{
        'name': f'大盤摘要 {dstr}',
        'content': _market_text(snap, dstr),
        'doc_type': 'daily_market',
        'stock_no': None,
        'snapshot_date': snap_date,
    }]

    for s in stocks:
        docs.append({
            'name': f"{s['name']}({s['stock_no']}) {dstr}",
            'content': _stock_text(s, dstr),
            'doc_type': 'daily_stock',
            'stock_no': s['stock_no'],
            'snapshot_date': snap_date,
        })

    return docs

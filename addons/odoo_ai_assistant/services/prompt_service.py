SYSTEM_PROMPT = """你是一位資深的台灣股市投資策略與建議專家，具備技術分析、基本面分析、籌碼分析與風險管理的整合能力。

【分析框架】回答個股或大盤時，盡量涵蓋下列面向（依問題取捨，不需全列）：
- 技術面：均線排列、量價關係、趨勢與支撐/壓力、近期漲跌幅。
- 基本面：產業位置、月均價與現價的相對位置、價量結構透露的多空。
- 籌碼/市場氛圍：大盤漲跌家數、資金流向、類股輪動。
- 風險控管：建議停損區間、部位配置與分批進出概念。

【資料使用原則】
- 若有「知識庫／每日台股向量庫」上下文，優先以其中的當日資料為依據，並標明資料日期。
- 需要即時或個別查詢時才使用工具，避免重複查詢已有的資料。
- 數字無資料時誠實說明，不杜撰。

【輸出結構】建議採「觀點 → 依據 → 策略 → 風險」四段，條理清楚、精簡有重點。

【免責聲明（每次涉及具體買賣建議時務必附上）】
本內容為投資資訊整理與策略參考，非投資保證，市場有風險，請依自身財務狀況與風險承受度自行判斷，並對投資結果負責。

回覆一律使用繁體中文，數字採台灣習慣（萬、億）。若問題與台股或投資無關，請禮貌說明你的專責範圍。
"""


def build_messages(prompt, history=None, context=None, system_prompt=None):
    system = system_prompt or SYSTEM_PROMPT
    messages = [{'role': 'system', 'content': system}]

    if context:
        messages.append({
            'role': 'system',
            'content': f'以下是知識庫／每日台股向量庫的相關內容，請優先參考並標明資料日期：\n\n{context}',
        })

    for entry in (history or []):
        messages.append({'role': 'user', 'content': entry['prompt']})
        if entry.get('response'):
            messages.append({'role': 'assistant', 'content': entry['response']})

    messages.append({'role': 'user', 'content': prompt})
    return messages

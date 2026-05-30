"""
LLMService — OpenAI + Google Gemini (google-genai SDK).
"""
import json

from .prompt_service import build_messages, SYSTEM_PROMPT

MAX_ITERATIONS = 2
# 限制單次回答長度，避免按 token 計費時產生超長（昂貴）輸出
MAX_OUTPUT_TOKENS = 2048


def _system_of(cfg):
    return cfg.get('system_prompt') or SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 429 配額錯誤解析（把 Google 回傳的 quota_id / retryDelay 攤出來）
# ---------------------------------------------------------------------------

def _extract_quota_info(e):
    """
    從 google-genai APIError 取出配額違規細節。
    優先讀結構化的 e.details（dict），失敗才退而解析 str(e) 內的字典字面值。
    回傳 dict，可能含 quota_id / retry_delay / model。
    """
    import ast

    payload = getattr(e, 'details', None)
    if not isinstance(payload, dict):
        s = str(e)
        start = s.find('{')
        if start != -1:
            try:
                payload = ast.literal_eval(s[start:])   # Google 字串是 Python dict repr（單引號）
            except (ValueError, SyntaxError):
                payload = None

    info = {}
    if isinstance(payload, dict):
        err = payload.get('error', payload)
        for d in (err.get('details') or []):
            t = str(d.get('@type', ''))
            if 'QuotaFailure' in t:
                for v in (d.get('violations') or []):
                    info['quota_id'] = v.get('quotaId') or v.get('quotaMetric') or info.get('quota_id')
                    dims = v.get('quotaDimensions') or {}
                    if dims.get('model'):
                        info['model'] = dims['model']
            elif 'RetryInfo' in t:
                info['retry_delay'] = d.get('retryDelay') or info.get('retry_delay')
    return info


def _quota_hint(quota_id):
    """依 quota_id 給出白話判讀。"""
    qid = quota_id or ''
    if 'PerDay' in qid:
        return '含 PerDay → 今日免費額度已用完，需等隔日重置，或升級付費層／改用其他供應商'
    if 'PerMinute' in qid:
        return '含 PerMinute → 短時間請求過密（每分鐘上限），等約 1 分鐘再試即可'
    if 'FreeTier' in qid:
        return '免費層配額限制 → 升級付費層或改用其他供應商'
    return '配額限制 → 依上方類型判斷是每日或每分鐘上限'


class LLMService:

    @staticmethod
    def chat(prompt, cfg, history=None, context=None):
        if cfg['provider'] == 'gemini':
            return _GeminiBackend.chat(prompt, cfg, history, context)
        if cfg['provider'] == 'claude':
            return _ClaudeBackend.chat(prompt, cfg, history, context)
        return _OpenAIBackend.chat(prompt, cfg, history, context)

    @staticmethod
    def chat_with_tools(prompt, tools, cfg, env, history=None, context=None):
        if cfg['provider'] == 'gemini':
            return _GeminiBackend.chat_with_tools(prompt, tools, cfg, env, history, context)
        if cfg['provider'] == 'claude':
            return _ClaudeBackend.chat_with_tools(prompt, tools, cfg, env, history, context)
        return _OpenAIBackend.chat_with_tools(prompt, tools, cfg, env, history, context)

    @staticmethod
    def chat_stream(prompt, cfg, history=None, context=None):
        """串流版一般對話：回傳一個逐段 yield 文字片段的 generator。
        供非同步 job 邊產生邊用 bus 推送（見 ai.chat.process_chat）。"""
        if cfg['provider'] == 'gemini':
            return _GeminiBackend.chat_stream(prompt, cfg, history, context)
        if cfg['provider'] == 'claude':
            return _ClaudeBackend.chat_stream(prompt, cfg, history, context)
        return _OpenAIBackend.chat_stream(prompt, cfg, history, context)


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

class _OpenAIBackend:

    @staticmethod
    def _client(cfg):
        import openai
        kwargs = {'api_key': cfg.get('api_key') or 'no-key', 'timeout': 60.0}
        if cfg.get('base_url'):
            kwargs['base_url'] = cfg['base_url']
        return openai.OpenAI(**kwargs)

    @staticmethod
    def chat(prompt, cfg, history, context):
        messages = build_messages(prompt, history=history, context=context,
                                  system_prompt=_system_of(cfg))
        client = _OpenAIBackend._client(cfg)
        resp = client.chat.completions.create(
            model=cfg['model'], messages=messages, max_tokens=MAX_OUTPUT_TOKENS,
        )
        return resp.choices[0].message.content

    @staticmethod
    def chat_stream(prompt, cfg, history, context):
        messages = build_messages(prompt, history=history, context=context,
                                  system_prompt=_system_of(cfg))
        client = _OpenAIBackend._client(cfg)
        stream = client.chat.completions.create(
            model=cfg['model'], messages=messages, max_tokens=MAX_OUTPUT_TOKENS,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    @staticmethod
    def chat_with_tools(prompt, tools, cfg, env, history, context):
        from .tool_service import ToolService

        client = _OpenAIBackend._client(cfg)
        messages = build_messages(prompt, history=history, context=context,
                                  system_prompt=_system_of(cfg))
        tool_log = []
        called_signatures = set()

        for _ in range(MAX_ITERATIONS):
            try:
                resp = client.chat.completions.create(
                    model=cfg['model'],
                    messages=messages,
                    tools=tools,
                    tool_choice='auto',
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
            except Exception as e:
                # 部分模型（如 Groq llama）在 tool-calling 時會退化成重複/不合法
                # 的 function call，後端回 400 tool_use_failed。此時不讓整個請求崩，
                # 改用「無工具」的一般回答優雅降級，至少給出可用答覆。
                if 'tool_use_failed' not in str(e):
                    raise
                fallback = client.chat.completions.create(
                    model=cfg['model'], messages=messages, max_tokens=MAX_OUTPUT_TOKENS,
                )
                return fallback.choices[0].message.content or '', tool_log
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or '', tool_log

            messages.append(msg)
            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                sig = (name, json.dumps(args, sort_keys=True))
                if sig in called_signatures:
                    result = {'note': '已查詢過，使用前次結果'}
                else:
                    called_signatures.add(sig)
                    result = ToolService.execute(env, name, args)
                tool_log.append({'tool': name, 'args': args, 'result': result})
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.id,
                    'content': json.dumps(result, ensure_ascii=False),
                })

        return '已達到最大工具呼叫次數', tool_log


# ---------------------------------------------------------------------------
# Claude backend — uses anthropic SDK
# ---------------------------------------------------------------------------

class _ClaudeBackend:

    @staticmethod
    def _client(cfg):
        import anthropic
        return anthropic.Anthropic(api_key=cfg['api_key'], timeout=60.0)

    @staticmethod
    def _split_messages(messages):
        """將 OpenAI 格式 messages 拆成 system string + 對話 list。"""
        system_parts = []
        chat_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_parts.append(m['content'])
            else:
                chat_messages.append({'role': m['role'], 'content': m['content']})
        return '\n\n'.join(system_parts), chat_messages

    @staticmethod
    def _openai_tools_to_claude(tools):
        result = []
        for t in tools:
            fn = t['function']
            result.append({
                'name': fn['name'],
                'description': fn['description'],
                'input_schema': fn.get('parameters', {'type': 'object', 'properties': {}}),
            })
        return result

    @staticmethod
    def chat(prompt, cfg, history, context):
        from .prompt_service import build_messages
        client = _ClaudeBackend._client(cfg)
        system_str, chat_messages = _ClaudeBackend._split_messages(
            build_messages(prompt, history=history, context=context,
                           system_prompt=_system_of(cfg))
        )
        resp = client.messages.create(
            model=cfg['model'],
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_str,
            messages=chat_messages,
        )
        return resp.content[0].text

    @staticmethod
    def chat_stream(prompt, cfg, history, context):
        from .prompt_service import build_messages
        client = _ClaudeBackend._client(cfg)
        system_str, chat_messages = _ClaudeBackend._split_messages(
            build_messages(prompt, history=history, context=context,
                           system_prompt=_system_of(cfg))
        )
        with client.messages.stream(
            model=cfg['model'],
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_str,
            messages=chat_messages,
        ) as stream:
            for text in stream.text_stream:
                if text:
                    yield text

    @staticmethod
    def chat_with_tools(prompt, tools, cfg, env, history, context):
        from .prompt_service import build_messages
        from .tool_service import ToolService

        client = _ClaudeBackend._client(cfg)
        claude_tools = _ClaudeBackend._openai_tools_to_claude(tools)
        system_str, claude_messages = _ClaudeBackend._split_messages(
            build_messages(prompt, history=history, context=context,
                           system_prompt=_system_of(cfg))
        )
        tool_log = []
        called_signatures = set()

        for _ in range(MAX_ITERATIONS):
            resp = client.messages.create(
                model=cfg['model'],
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_str,
                messages=claude_messages,
                tools=claude_tools,
            )

            if resp.stop_reason != 'tool_use':
                text = next((b.text for b in resp.content if hasattr(b, 'text')), '')
                return text, tool_log

            # 把整個 assistant 回應（含 tool_use blocks）加入對話
            claude_messages.append({'role': 'assistant', 'content': resp.content})

            tool_results = []
            for block in resp.content:
                if block.type != 'tool_use':
                    continue
                name = block.name
                args = dict(block.input)
                sig = (name, json.dumps(args, sort_keys=True))
                if sig in called_signatures:
                    result = {'note': '已查詢過，使用前次結果'}
                else:
                    called_signatures.add(sig)
                    result = ToolService.execute(env, name, args)
                tool_log.append({'tool': name, 'args': args, 'result': result})
                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': block.id,
                    'content': json.dumps(result, ensure_ascii=False),
                })

            claude_messages.append({'role': 'user', 'content': tool_results})

        return '已達到最大工具呼叫次數', tool_log


# ---------------------------------------------------------------------------
# Gemini backend — uses new google-genai SDK
# ---------------------------------------------------------------------------

class _GeminiBackend:

    @staticmethod
    def _client(cfg):
        from google import genai
        from google.genai import types
        # timeout=30s；新 SDK 對 REST 呼叫不做自動 retry，429 直接拋例外
        return genai.Client(api_key=cfg['api_key'])

    @staticmethod
    def _raise_if_rate_limited(e):
        is_429 = getattr(e, 'code', None) == 429
        msg = str(e).lower()
        if not (is_429 or '429' in msg or 'resource_exhausted' in msg or 'quota' in msg):
            return

        info = _extract_quota_info(e)
        parts = ['Gemini API 配額限制（429 RESOURCE_EXHAUSTED）。']
        if info.get('quota_id'):
            parts.append('配額類型：%s' % info['quota_id'])
        if info.get('model'):
            parts.append('受限模型：%s' % info['model'])
        if info.get('retry_delay'):
            parts.append('Google 建議重試間隔：%s' % info['retry_delay'])
        parts.append('判讀：%s' % _quota_hint(info.get('quota_id')))
        if not info.get('quota_id'):
            parts.append(
                '（Google 未回傳配額細項；最常見是新金鑰背後的 GCP 專案配額為 0，'
                '或免費層當日請求數已用完。）'
            )
        raise RuntimeError('\n'.join(parts)) from e

    @staticmethod
    def _build_contents(prompt, history, context):
        contents = []
        if context:
            contents += [
                {'role': 'user',  'parts': [{'text': f'以下是參考文件：\n{context}'}]},
                {'role': 'model', 'parts': [{'text': '已收到，我會優先參考這些內容。'}]},
            ]
        for entry in (history or []):
            contents.append({'role': 'user',  'parts': [{'text': entry['prompt']}]})
            if entry.get('response'):
                contents.append({'role': 'model', 'parts': [{'text': entry['response']}]})
        contents.append({'role': 'user', 'parts': [{'text': prompt}]})
        return contents

    @staticmethod
    def _openai_tools_to_genai(tools):
        """Convert OpenAI-format tool list to google-genai Tool object."""
        from google.genai import types

        def _schema(d):
            if not d:
                return None
            type_str = d.get('type', 'object').upper()
            props = {k: _schema(v) for k, v in d.get('properties', {}).items()} or None
            return types.Schema(
                type=type_str,
                description=d.get('description', ''),
                properties=props,
                required=d.get('required') or None,
            )

        declarations = []
        for t in tools:
            fn = t['function']
            declarations.append(types.FunctionDeclaration(
                name=fn['name'],
                description=fn['description'],
                parameters=_schema(fn.get('parameters')),
            ))
        return types.Tool(function_declarations=declarations)

    @staticmethod
    def _generate(client, model, contents, config, attempt=0):
        """呼叫 Gemini，timeout 時自動重試一次。"""
        import time
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config,
            )
        except Exception as e:
            _GeminiBackend._raise_if_rate_limited(e)
            err = str(e).lower()
            if attempt == 0 and ('timed out' in err or 'timeout' in err or 'read operation' in err):
                time.sleep(2)
                return _GeminiBackend._generate(client, model, contents, config, attempt=1)
            raise

    @staticmethod
    def chat(prompt, cfg, history, context):
        from google.genai import types

        client = _GeminiBackend._client(cfg)
        contents = _GeminiBackend._build_contents(prompt, history, context)
        config = types.GenerateContentConfig(
            system_instruction=_system_of(cfg),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        response = _GeminiBackend._generate(client, cfg['model'], contents, config)
        try:
            return response.text
        except Exception:
            return str(response)

    @staticmethod
    def chat_stream(prompt, cfg, history, context):
        from google.genai import types

        client = _GeminiBackend._client(cfg)
        contents = _GeminiBackend._build_contents(prompt, history, context)
        config = types.GenerateContentConfig(
            system_instruction=_system_of(cfg),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        try:
            stream = client.models.generate_content_stream(
                model=cfg['model'], contents=contents, config=config,
            )
            for chunk in stream:
                try:
                    text = chunk.text
                except Exception:
                    text = None
                if text:
                    yield text
        except Exception as e:
            _GeminiBackend._raise_if_rate_limited(e)
            raise

    @staticmethod
    def chat_with_tools(prompt, tools, cfg, env, history, context):
        from google.genai import types
        from .tool_service import ToolService

        client = _GeminiBackend._client(cfg)
        gemini_tool = _GeminiBackend._openai_tools_to_genai(tools)
        contents = _GeminiBackend._build_contents(prompt, history, context)
        tool_log = []
        called_signatures = set()

        config = types.GenerateContentConfig(
            system_instruction=_system_of(cfg),
            tools=[gemini_tool],
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        for _ in range(MAX_ITERATIONS):
            response = _GeminiBackend._generate(client, cfg['model'], contents, config)

            candidate = response.candidates[0]
            calls = [
                p.function_call
                for p in candidate.content.parts
                if p.function_call and p.function_call.name
            ]

            if not calls:
                try:
                    return response.text, tool_log
                except Exception:
                    return str(response), tool_log

            # Append model turn
            contents.append(candidate.content)

            # Execute tools and build response parts
            response_parts = []
            for fc in calls:
                name = fc.name
                args = dict(fc.args)
                sig = (name, json.dumps(args, sort_keys=True))
                if sig in called_signatures:
                    result = {'note': '已查詢過，使用前次結果'}
                else:
                    called_signatures.add(sig)
                    result = ToolService.execute(env, name, args)
                tool_log.append({'tool': name, 'args': args, 'result': result})
                response_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={'result': result},
                    )
                )

            contents.append(
                types.Content(role='user', parts=response_parts)
            )

        return '已達到最大工具呼叫次數', tool_log

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
    def chat_with_tools(prompt, tools, cfg, env, history, context):
        from .tool_service import ToolService

        client = _OpenAIBackend._client(cfg)
        messages = build_messages(prompt, history=history, context=context,
                                  system_prompt=_system_of(cfg))
        tool_log = []
        called_signatures = set()

        for _ in range(MAX_ITERATIONS):
            resp = client.chat.completions.create(
                model=cfg['model'],
                messages=messages,
                tools=tools,
                tool_choice='auto',
                max_tokens=MAX_OUTPUT_TOKENS,
            )
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
        msg = str(e).lower()
        if '429' in msg or 'resource_exhausted' in msg or 'quota' in msg:
            raise RuntimeError(
                'Gemini API 配額已用完（429）。請稍等 1 分鐘後再試，'
                '或至 Google AI Studio 確認免費配額。'
            ) from e

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

/** @odoo-module */

import { Component, useState, useRef, onMounted, onWillUnmount } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';

export class AIChatWidget extends Component {
    static template = 'odoo_ai_assistant.ChatWidget';

    setup() {
        this.rpc = useService('rpc');
        this.user = useService('user');
        this.busService = useService('bus_service');
        this.messagesRef = useRef('messages');

        // bus 事件可能在 /ai/chat 回傳 message_id 之前就到（job 搶先送 status），
        // 此時先暫存，待 placeholder 取得 id 後再補放。
        this._busBuffer = {};

        this.state = useState({
            prompt: '',
            messages: [],
            loading: false,
            streamingId: null,   // 目前正在串流的 message_id（決定是否顯示「停止」）
            useRag: true,
            useTools: true,
            provider: '',
            model: '',
            providers: [],
            // 對話側邊欄
            sessions: [],
            currentSessionId: null,
            sidebarOpen: true,
        });

        // 元件掛載即訂閱使用者專屬頻道（早於送出第一則訊息，避免漏接開頭 delta）。
        // Odoo 16 的 bus_service 用 addEventListener("notification")，detail 是
        // 一組 {type, payload}；type 為 _sendone 的第 2 參數（這裡是 'ai_chat'）。
        this._onNotification = ({ detail: notifications }) => {
            for (const { type, payload } of notifications) {
                if (type === 'ai_chat') {
                    this._onBus(payload);
                }
            }
        };
        this.busService.addEventListener('notification', this._onNotification);
        this._channel = `ai_chat_${this.user.userId}`;
        this.busService.addChannel(this._channel);

        onMounted(async () => {
            await this.loadConfig();
            await this.loadSessions();
            this._scrollToBottom();
        });

        onWillUnmount(() => {
            this.busService.removeEventListener('notification', this._onNotification);
            this.busService.deleteChannel(this._channel);
        });
    }

    async loadConfig() {
        try {
            const cfg = await this.rpc('/ai/config', {});
            this.state.provider = cfg.provider;
            this.state.model = cfg.model;
            this.state.providers = cfg.providers || [];
        } catch (e) {
            // 設定載入失敗不阻斷聊天
        }
    }

    // ── bus 即時推送處理 ────────────────────────────────────────

    _onBus(payload) {
        if (!payload || payload.type === undefined) return;
        const id = payload.message_id;
        const msg = this.state.messages.find(
            (m) => m.id === id && (m.role === 'assistant' || m.role === 'error'));
        if (!msg) {
            // placeholder 尚未取得 id，先暫存
            (this._busBuffer[id] = this._busBuffer[id] || []).push(payload);
            return;
        }
        this._applyBus(msg, payload);
    }

    _applyBus(msg, payload) {
        if (payload.type === 'status') {
            msg.statusText = payload.text || '';
        } else if (payload.type === 'delta') {
            msg.statusText = '';
            msg.content += payload.text || '';
        } else if (payload.type === 'done') {
            msg.streaming = false;
            msg.statusText = '';
            if (payload.model) msg.model = payload.model;
            this._endStreaming(msg.id);
            // 新對話的標題在後端產生，完成後刷新側邊欄
            this.loadSessions();
        } else if (payload.type === 'error') {
            msg.role = 'error';
            msg.content = payload.text || '（發生錯誤）';
            msg.streaming = false;
            msg.statusText = '';
            this._endStreaming(msg.id);
        }
        this._scrollToBottom();
    }

    _endStreaming(id) {
        if (this.state.streamingId === id) {
            this.state.streamingId = null;
            this.state.loading = false;
        }
    }

    // ── 對話側邊欄 ──────────────────────────────────────────────

    async loadSessions() {
        try {
            this.state.sessions = await this.rpc('/ai/sessions', {});
        } catch (e) {
            this.state.sessions = [];
        }
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
    }

    // 開新對話：清空畫面，下一則送出時後端會自動建立 session
    newChat() {
        this.state.currentSessionId = null;
        this.state.messages = [];
        this.state.prompt = '';
    }

    async selectSession(sessionId) {
        if (this.state.loading) return;
        try {
            const res = await this.rpc('/ai/session/messages', { session_id: sessionId });
            this.state.messages = res.messages || [];
            this.state.currentSessionId = sessionId;
            this._scrollToBottom();
        } catch (e) {
            // ignore
        }
    }

    async deleteSession(sessionId, ev) {
        ev.stopPropagation();
        try {
            await this.rpc('/ai/session/delete', { session_id: sessionId });
        } catch (e) {
            // ignore
        }
        if (sessionId === this.state.currentSessionId) {
            this.newChat();
        }
        await this.loadSessions();
    }

    // ── 模型/供應商切換 ─────────────────────────────────────────

    get currentProvider() {
        return this.state.providers.find((x) => x.value === this.state.provider);
    }

    get currentModels() {
        const p = this.currentProvider;
        return p ? p.models : [];
    }

    // 目前選的供應商是否已設定金鑰（Ollama 不需）
    get currentHasKey() {
        const p = this.currentProvider;
        return p ? p.has_key : true;
    }

    // 純前端切換：每題獨立，不寫回全域設定
    onProviderChange(ev) {
        this.state.provider = ev.target.value;
        const p = this.currentProvider;
        this.state.model = p && p.models.length ? p.models[0].value : '';
    }

    onModelChange(ev) {
        this.state.model = ev.target.value;
    }

    async sendMessage() {
        const prompt = this.state.prompt.trim();
        if (!prompt || this.state.loading) return;

        this.state.messages.push({ role: 'user', content: prompt });
        this.state.prompt = '';
        this.state.loading = true;

        // 先放一個串流中的 assistant 泡泡；id 待後端回傳後補上
        const placeholder = {
            role: 'assistant', content: '', model: '',
            id: null, streaming: true, statusText: '排隊中…',
        };
        this.state.messages.push(placeholder);
        this._scrollToBottom();

        try {
            const result = await this.rpc('/ai/chat', {
                prompt,
                use_rag: this.state.useRag,
                use_tools: this.state.useTools,
                provider: this.state.provider,
                model: this.state.model,
                session_id: this.state.currentSessionId,
            });

            // 立即錯誤（例如未設金鑰）：直接把泡泡轉成錯誤
            if (result.error) {
                placeholder.role = 'error';
                placeholder.content = result.error;
                placeholder.streaming = false;
                placeholder.statusText = '';
                this.state.loading = false;
                return;
            }

            placeholder.id = result.message_id;
            placeholder.model = result.model;
            this.state.streamingId = result.message_id;
            this.state.currentSessionId = result.session_id;
            if (result.is_new_session) {
                await this.loadSessions();
            }

            // 補放在 id 就緒前已抵達的 bus 事件
            const buffered = this._busBuffer[result.message_id];
            if (buffered) {
                buffered.forEach((p) => this._applyBus(placeholder, p));
                delete this._busBuffer[result.message_id];
            }
        } catch (e) {
            placeholder.role = 'error';
            placeholder.content = String(e);
            placeholder.streaming = false;
            placeholder.statusText = '';
            this.state.loading = false;
        }
    }

    async stopGeneration() {
        const id = this.state.streamingId;
        if (!id) return;
        try {
            await this.rpc('/ai/chat/stop', { message_id: id });
        } catch (e) {
            // ignore；job 偵測到 cancel 後會自行收斂
        }
    }

    onKeyDown(ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    _scrollToBottom() {
        const el = this.messagesRef.el;
        if (el) el.scrollTop = el.scrollHeight;
    }
}

registry.category('actions').add('ai_chat_widget', AIChatWidget);

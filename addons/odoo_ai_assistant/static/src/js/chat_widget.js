/** @odoo-module */

import { Component, useState, useRef, onMounted } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';

export class AIChatWidget extends Component {
    static template = 'odoo_ai_assistant.ChatWidget';

    setup() {
        this.rpc = useService('rpc');
        this.messagesRef = useRef('messages');

        this.state = useState({
            prompt: '',
            messages: [],
            loading: false,
            useRag: true,
            useTools: true,
            provider: '',
            model: '',
            providers: [],
        });

        onMounted(async () => {
            await this.loadConfig();
            this._scrollToBottom();
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
        this._scrollToBottom();

        try {
            const result = await this.rpc('/ai/chat', {
                prompt,
                use_rag: this.state.useRag,
                use_tools: this.state.useTools,
                provider: this.state.provider,
                model: this.state.model,
            });

            if (result.error) {
                this.state.messages.push({ role: 'error', content: result.error });
            } else {
                this.state.messages.push({
                    role: 'assistant',
                    content: result.response,
                    model: result.model,
                });
            }
        } catch (e) {
            this.state.messages.push({ role: 'error', content: String(e) });
        } finally {
            this.state.loading = false;
            this._scrollToBottom();
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

/**
 * AIGEME 前端全局状态管理
 * 分三层：shared（共享） / chat（VN 聊天） / pet（预留）
 */
const AIGEME = {
    // ══════════════════════════════
    // 共享状态
    // ══════════════════════════════
    shared: {
        ws: null,
        connected: false,
        screen: 'title-screen',  // 设计稿 screen ID
        mode: 'chat',
        theme: 'default',
        settings: {
            wsUrl: 'ws://localhost:8765',
            ttsEnabled: false,
        },
    },

    // ══════════════════════════════
    // Chat 模式状态
    // ══════════════════════════════
    chat: {
        currentChar: null,     // { id, name, expressions? }
        characters: [],        // 可用角色列表
        dialogue: [],          // 对话历史 [{ role, content }]
        currentMessage: '',
        isStreaming: false,
        turnEnded: false,      // turn_end 信号已到
        turnCancelled: false,  // 本轮是否被取消

        thinking: '',
        thinkingVisible: false,

        // 新增：
        thinkHistory: [],        // 当前轮思维链历史
        thinkingAppended: false,  // 标记思维链面板是否已追加过内容
        toolCallCount: 0,         // 工具调用计数
        thinkPanelVisible: false, // 设计稿思维链面板显隐

        // 轮次ID（每次切角色/新对话递增），用于让过期的打字机/speech回调自动失效
        turnId: 0,

        // 图片上传
        pendingImages: [],       // 待发送的图片 base64 数组
        // lastToolRound: null,    // 已移除：服务端内存系统管理 Ms[]/tool/say 的拼接，前端不需要额外传递
        panels: {
            left: false,
            right: false,
            history: false,
        },
        currentConvDate: null,  // 当前选中的对话日期

        expression: 'default',
        scene: 'default',

        typewriter: {
            text: '',
            isTyping: false,
            speed: 40,          // ms/字
        },
    },

    // ══════════════════════════════
    // Pet 模式（预留）
    // ══════════════════════════════
    pet: null,

    // ══════════════════════════════
    // 状态操作函数
    // ══════════════════════════════

    /** 切换屏幕 */
    setScreen: function(screen) {
        document.querySelectorAll('.screen').forEach(el => {
            el.classList.remove('active');
            el.style.opacity = '0';
        });
        const target = document.getElementById(screen);
        if (target) {
            target.classList.add('active');
            requestAnimationFrame(() => target.style.opacity = '1');
        }
        this.shared.screen = screen;

        if (screen === 'char-select') {
            this.loadCharacters();
        } else if (screen === 'screen-chat' && this.chat.currentChar) {
            // 进入聊天屏时确保立绘加载
            AIGEME_UI.setTachie('default');
        }
    },

    /** 处理"开始聊天"按钮点击 — 检查最近角色 */
    handleStartChat: async function() {
        var savedId = localStorage.getItem('aigeme-last-char-id');
        var savedName = localStorage.getItem('aigeme-last-char-name');
        if (savedId && savedName) {
            // 先加载角色列表（含 expressions），再切换到保存的角色
            await this.loadCharacters();
            var charData = this.chat.characters.find(function(c) { return c.id === savedId; });
            if (charData) {
                this.selectCharacter(charData);
            } else {
                this.switchCharacter(savedId, savedName);
            }
            this.setScreen('screen-chat');
            // 加载最新对话历史
            this._loadLatestConversation();
        } else {
            // 无最近角色，跳到角色选择
            this.setScreen('char-select');
        }
    },

    /** 加载角色列表 */
    loadCharacters: async function() {
        const grid = document.getElementById('char-grid');
        if (!grid) return;

        try {
            const resp = await fetch('/api/characters');
            const chars = await resp.json();
            this.chat.characters = chars;

            grid.innerHTML = '';
            for (const c of chars) {
                const card = document.createElement('div');
                card.className = 'char-card';
                card.dataset.charId = c.id;
                card.innerHTML = `
                    <div class="char-avatar">${c.emoji || '🎭'}</div>
                    <div class="char-name">${c.name}</div>
                    <div class="char-desc">${c.description || ''}</div>
                `;
                card.onclick = () => {
                    this.selectCharacter(c);
                };
                grid.appendChild(card);
            }
        } catch (e) {
            console.error('加载角色列表失败:', e);
        }
    },

    /** 选择角色 */
    selectCharacter: function(charData) {
        document.querySelectorAll('.char-card').forEach(el => el.classList.remove('selected'));
        const card = document.querySelector(`.char-card[data-char-id="${charData.id}"]`);
        if (card) card.classList.add('selected');
        this.chat.currentChar = {
            id: charData.id,
            name: charData.name,
            expressions: charData.expressions || null,
            tachie_dir: charData.tachie_dir || ('tachi-e/' + charData.id)
        };

        // 设置角色名到设计稿的顶栏
        const tbName = document.getElementById('tb-name');
        if (tbName) tbName.textContent = charData.name;

        // 保存最近角色到 localStorage（供"开始聊天"按钮使用）
        localStorage.setItem('aigeme-last-char-id', charData.id);
        localStorage.setItem('aigeme-last-char-name', charData.name);

        // 设置默认立绘
        AIGEME_UI.setTachie('default');

        // 设计稿风格：选择角色后自动进入主屏
        this.setScreen('screen-chat');
        this.connectWebSocket();
        this._loadLatestConversation();
        // 加载技能和记忆面板
        this.loadSkills(charData.id);
        this.loadMemoryPanel(charData.id);
    },

    /** 开始对话 */
    startChat: function() {
        if (!this.chat.currentChar) return;
        this.setScreen('screen-chat');
        this.connectWebSocket();
        this._loadLatestConversation();
    },

    /** 自动加载最新对话 */
    _loadLatestConversation: async function() {
        const charId = this.chat.currentChar?.id;
        if (!charId) return;
        try {
            const resp = await fetch(`/api/conversations/${charId}`);
            const convs = await resp.json();
            if (convs && convs.length > 0) {
                const latest = convs[0];
                await this.loadConversation(charId, latest.date);
            }
        } catch (e) {
            console.log('无历史对话，开始新对话');
        }
    },

    /** 开始新对话 */
    startNewChat: function() {
        if (this.shared.connected) {
            this.shared.ws.close();
        }
        // 递增 turnId，使所有进行中的打字机/speech回调立即失效
        this.chat.turnId = (this.chat.turnId || 0) + 1;
        this.chat.dialogue = [];
        this.chat.currentMessage = '';
        this.chat.isStreaming = false;
        this.chat.typewriter.isTyping = false;
        this.chat.thinking = '';
        this.chat.thinkingVisible = false;
        this.chat.expression = 'default';
        this.chat.turnEnded = false;
        this.chat.turnCancelled = false;
        this.chat.currentConvDate = null;

        const chList = document.getElementById('ch-list');
        if (chList) chList.innerHTML = '';

        document.getElementById('thinking-panel')?.classList.add('hidden');
        document.getElementById('tool-status')?.classList.add('hidden');
        document.getElementById('current-output')?.style.setProperty('display', 'none');
        document.getElementById('output-text') ? document.getElementById('output-text').innerHTML = '' : null;
        const inputMsg = document.getElementById('input-msg');
        const userInput = document.getElementById('user-input');
        if (inputMsg) inputMsg.disabled = false;
        if (userInput) userInput.disabled = false;

        if (this.chat.currentChar) {
            this.connectWebSocket();
        }
    },

    /** 切换角色 — 完整切换流程 */
    switchCharacter: function(charId, charName) {
        // 1. 断开现有 WS 连接
        if (this.shared.ws) {
            WSClient.disconnect();
        }
        // 2. 清空消息列表
        const chList = document.getElementById('ch-list');
        if (chList) chList.innerHTML = '';
        // 3. 清空对话状态
        this.chat.dialogue = [];
        this.chat.currentMessage = '';
        // 4. 强制停止打字机（递增 turnId 使所有进行中的打字机/speech回调失效）
        this.chat.turnId = (this.chat.turnId || 0) + 1;
        this.chat.typewriter.isTyping = false;
        this.chat.isStreaming = false;
        this.chat.turnEnded = false;
        this.chat.turnCancelled = false;
        // 清空 chat-text 和 output-text，防止旧内容残留
        var chatText = document.getElementById('chat-text');
        if (chatText) chatText.innerHTML = '';
        var outputText = document.getElementById('output-text');
        if (outputText) outputText.innerHTML = '';
        // 5. 设置新角色（尝试从 characters 列表获取 expressions）
        var charInfo = this.chat.characters.find(function(c) { return c.id === charId; });
        this.chat.currentChar = {
            id: charId,
            name: charName,
            expressions: charInfo ? (charInfo.expressions || null) : null
        };
        const tbName = document.getElementById('tb-name');
        if (tbName) tbName.textContent = charName;

        // 保存最近角色到 localStorage
        localStorage.setItem('aigeme-last-char-id', charId);
        localStorage.setItem('aigeme-last-char-name', charName);
        // 清空上一轮的 tool 上下文（角色切换后不再沿用）
        // 服务端内存系统管理 Ms[]/tool/say，前端无需额外清理
        // 清理 UI
        document.getElementById('thinking-panel')?.classList.add('hidden');
        document.getElementById('tool-status')?.classList.add('hidden');
        document.getElementById('current-output')?.style.setProperty('display', 'none');
        document.getElementById('output-text') ? document.getElementById('output-text').innerHTML = '' : null;
        var thinkContent = document.getElementById('think-content');
        if (thinkContent) thinkContent.innerHTML = '';
        var toolCallsEl = document.getElementById('tool-calls');
        if (toolCallsEl) toolCallsEl.innerHTML = '';
        AIGEME.chat.thinkingAppended = false;
        const inputMsg = document.getElementById('input-msg');
        const userInput = document.getElementById('user-input');
        if (inputMsg) inputMsg.disabled = false;
        if (userInput) userInput.disabled = false;
        // 重置发送按钮状态
        AIGEME._updateStopButton();
        // 6. 建立新 WS 连接（_loadLatestConversation 会在加载历史后保留此连接）
        WSClient.connect(charId);
    },

    /** 添加消息到对话列表 */
    addMessage: function(msg) {
        this.chat.dialogue.push(msg);
    },

    /** 追加流式文本 */
    appendStream: function(text) {
        this.chat.currentMessage += replaceEmojiTags(text);
    },

    /** 设置表情 */
    setExpression: function(expr) {
        this.chat.expression = expr;
    },

    /** 流式结束 */
    finishStream: function(cancelled) {
        if (this.chat.currentMessage) {
            this.addMessage({
                role: 'assistant',
                content: this.chat.currentMessage,
                cancelled: !!cancelled,
            });
            // ★ 修复：将 AI 回复渲染到对话历史抽屉（#ch-list）
            var chList = document.getElementById('ch-list');
            if (chList) {
                var name = this.chat.currentChar ? this.chat.currentChar.name : '';
                var turnId = this.chat.turnId || 0;
                chList.insertAdjacentHTML('beforeend', [
                    '<div class="ch-msg ch-msg-assistant" data-turn-id="' + turnId + '">',
                    '  <div class="ch-msg-name">', AIGEME_UI._escapeHtml(name),
                    '    <button class="tts-replay-btn" data-turn-id="' + turnId + '" title="重播语音">🔁</button>',
                    '  </div>',
                    '  <div class="ch-msg-text">', AIGEME_UI._escapeHtml(replaceEmojiTags(this.chat.currentMessage)), '</div>',
                    '</div>'
                ].join(''));
                chList.scrollTop = chList.scrollHeight;
            }
        }
        this.chat.currentMessage = '';
        this.chat.isStreaming = false;
        this.chat.typewriter.isTyping = false;
        this.chat.turnEnded = false;
        this.chat.turnCancelled = false;
        this._updateStopButton();
    },

    /** 取消当前流式输出 */
    cancelStream: function() {
        // 发送取消消息到 WS
        if (this.shared.ws && this.shared.connected) {
            this.shared.ws.send(JSON.stringify({ type: 'cancel' }));
        }
        // 强制停止打字机效果
        this.chat.typewriter.isTyping = false;
        // 完成流（标记为 cancelled）
        this.finishStream(true);
        // 隐藏停止按钮
        this._updateStopButton();
    },

    /** 调整立绘显示比例（3:4裁剪 + 等比缩放填满舞台） */
    adjustSpriteRatio: function() {
        const spriteImg = document.getElementById('sprite-img');
        const spriteWrap = document.getElementById('sprite-wrap');
        const stage = document.getElementById('stage');
        if (!spriteImg || !spriteWrap || !stage) return;

        // 获取立绘自然宽高
        const naturalWidth = spriteImg.naturalWidth;
        const naturalHeight = spriteImg.naturalHeight;
        if (naturalWidth <= 0 || naturalHeight <= 0) return;

        // 立绘容器尺寸
        const containerHeight = stage.clientHeight;

        // 立绘图片框尺寸 — 高度 = 宽度的 4/3 倍
        const frameHeightByWidth = naturalWidth * (4 / 3);

        if (naturalHeight <= frameHeightByWidth) {
            spriteWrap.style.width = naturalWidth + 'px';
            spriteWrap.style.height = naturalHeight + 'px';
        } else {
            spriteWrap.style.width = naturalWidth + 'px';
            spriteWrap.style.height = frameHeightByWidth + 'px';
        }

        // 等比缩放到填满立绘容器高度
        const scale = containerHeight / parseFloat(spriteWrap.style.height);
        const scaledWidth = parseFloat(spriteWrap.style.width) * scale;
        spriteWrap.style.width = scaledWidth + 'px';
        spriteWrap.style.height = containerHeight + 'px';

        // PNG图片填满sprite-wrap
        spriteImg.style.width = '100%';
        spriteImg.style.height = '100%';
    },

    /** 更新发送/停止按钮显隐 */
    _updateStopButton: function() {
        var isBusy = this.chat.isStreaming || this.chat.typewriter.isTyping;
        // 设计稿 send-btn（合一按钮：内含 icon-send 和 icon-stop）
        var sendBtn = document.getElementById('send-btn');
        if (sendBtn) {
            var iconSend = sendBtn.querySelector('.icon-send');
            var iconStop = sendBtn.querySelector('.icon-stop');
            if (iconSend) iconSend.style.display = isBusy ? 'none' : '';
            if (iconStop) iconStop.style.display = isBusy ? '' : 'none';
        }
        // 旧独立停止按钮
        var stopBtn = document.getElementById('stop-btn');
        if (stopBtn) {
            stopBtn.style.display = isBusy ? '' : 'none';
        }
    },

    /** 切换历史面板 */
    toggleHistoryPanel: function() {
        const panel = document.getElementById('history-panel');
        if (!panel) return;
        panel.classList.toggle('open');
        if (panel.classList.contains('open')) {
            if (this.chat.currentChar) {
                this.loadConversations(this.chat.currentChar.id);
            }
        }
    },

    /** 加载历史会话列表 */
    loadConversations: async function(charId) {
        try {
            const resp = await fetch(`/api/conversations/${charId}`);
            const convs = await resp.json();
            AIGEME_UI.renderHistoryPanel(convs, AIGEME.chat.currentConvDate);
        } catch (e) {
            console.error('加载历史会话失败:', e);
        }
    },

    /** 获取指定日期的完整对话 — 切换对话历史时断开当前 WS */
    loadConversation: async function(charId, date) {
        if (this.shared.ws) {
            WSClient.disconnect();
        }
        this.chat.currentConvDate = date;
        try {
            const resp = await fetch(`/api/conversations/${charId}/${date}`);
            const records = await resp.json();
            AIGEME_UI.loadHistory(charId, date, records);
        } catch (e) {
            console.error('加载历史对话失败:', e);
        }
    },

    /** 切换面板（设计稿侧面板） */
    togglePanel: function(name) {
        const panelId = name === 'left' ? 'panel-left' : 'panel-right';
        const panel = document.getElementById(panelId);
        if (panel) {
            panel.classList.toggle('open');
            this.chat.panels[name] = panel.classList.contains('open');
        }
    },

    /** 切换主题 */
    toggleTheme: function() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('aigeme-theme', next);
    },

    /** 应用已保存的主题偏好 */
    applyTheme: function() {
        const saved = localStorage.getItem('aigeme-theme');
        if (saved) {
            document.documentElement.setAttribute('data-theme', saved);
        }
    },

    /** 发送消息 — 兼容新旧输入框 */
    sendMessage: function() {
        const userInput = document.getElementById('user-input');
        const inputMsg = document.getElementById('input-msg');
        const input = userInput || inputMsg;
        if (!input) return;

        const text = input.value.trim();
        if (!text || this.chat.isStreaming) return;
        input.value = '';

        // 同步清空另一个输入框
        if (userInput && inputMsg) {
            userInput.value = '';
            inputMsg.value = '';
            userInput.style.height = 'auto';
        }

        // 添加用户消息
        this.addMessage({ role: 'user', content: text });
        AIGEME_UI.renderUserMessage(text);

        // 清空上一轮的思维链 和 语音内容
        const thinkContent = document.getElementById('think-content');
        if (thinkContent) thinkContent.innerHTML = '';
        const toolCalls = document.getElementById('tool-calls');
        if (toolCalls) toolCalls.innerHTML = '';
        // 清空上一轮的 plan 卡片
        const oldPlanContainers = document.querySelectorAll('#chat-box > .plan-container');
        for (const el of oldPlanContainers) el.remove();
        BlockRenderer._planRenderers.clear();
        const thinkTitle = document.getElementById('think-title');
        if (thinkTitle) thinkTitle.textContent = '思考中...';
        AIGEME.chat.thinkingAppended = false;
        AIGEME.chat.toolCallCount = 0;

        // 清空上一轮的语音输出（避免新旧内容拼接）
        var chatText = document.getElementById('chat-text');
        if (chatText) chatText.innerHTML = '';
        var outputText = document.getElementById('output-text');
        if (outputText) outputText.innerHTML = '';
        var output = document.getElementById('current-output');
        if (output) output.style.display = 'none';

        // 标记流式开始，立即切换按钮为停止状态
        this.chat.isStreaming = true;
        // 新一轮发送时更新 turnId 及 speechTurnId，使本轮 speech block 能正常通过检查
        this.chat.turnId = (this.chat.turnId || 0) + 1;
        this.chat._speechTurnId = this.chat.turnId;
        this._updateStopButton();

        // TTS：中断当前播放
        if (typeof TTSPlayer !== 'undefined') {
            TTSPlayer.interrupt(this.chat.turnId);
        }

        // 通过 WebSocket 发送（服务端内存系统管理 Ms[]/tool/say 的拼接）
        if (this.shared.ws && this.shared.connected) {
            var messagePayload = {
                type: 'user_message',
                content: text,
                character_id: this.chat.currentChar ? this.chat.currentChar.id : 'ario',
                mode: 'single',
                images: [],
                stream: document.getElementById('stream-toggle')?.checked ?? true,
                tts_enabled: AIGEME.shared.settings.ttsEnabled || false,
                tts_mode: (document.getElementById('tts-mode')?.value) || 'preset',
                tts_voice: (document.getElementById('tts-voice')?.value) || '冰糖',
                tts_tone: (document.getElementById('tts-tone')?.value) || '自然温和',
            };
            // 附带待发送的图片
            if (this.chat.pendingImages && this.chat.pendingImages.length > 0) {
                messagePayload.images = this.chat.pendingImages.slice();
                this.chat.pendingImages = [];
                this._updateImagePreview();
            }
            this.shared.ws.send(JSON.stringify(messagePayload));
            if (window.soundManager) {
                window.soundManager.play('message_send');
            }
        }
    },

    /** 连接 WebSocket */
    connectWebSocket: function() {
        if (!this.chat.currentChar) return;
        WSClient.connect(this.chat.currentChar.id);
    },

    /** 添加待发送图片（base64） */
    _addImage: function(base64Data) {
        if (!this.chat.pendingImages) this.chat.pendingImages = [];
        this.chat.pendingImages.push(base64Data);
        this._updateImagePreview();
    },

    /** 移除指定索引的待发送图片 */
    _removeImage: function(index) {
        if (!this.chat.pendingImages) return;
        this.chat.pendingImages.splice(index, 1);
        this._updateImagePreview();
    },

    /** 更新图片预览 UI */
    _updateImagePreview: function() {
        var container = document.getElementById('image-preview');
        if (!container) return;
        var images = this.chat.pendingImages || [];
        if (images.length === 0) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }
        container.style.display = 'flex';
        container.innerHTML = '';
        images.forEach(function(b64, idx) {
            var item = document.createElement('div');
            item.className = 'image-preview-item';
            item.innerHTML =
                '<img src="data:image/png;base64,' + b64 + '" class="image-preview-thumb">' +
                '<button class="image-preview-remove" data-idx="' + idx + '">×</button>';
            item.querySelector('.image-preview-remove').onclick = function() {
                AIGEME._removeImage(idx);
            };
            container.appendChild(item);
        });
    },

    /** 加载角色技能列表到侧面板 */
    loadSkills: async function(charId) {
        var list = document.querySelector('.sp-page[data-tab="skills"] .sk-list');
        if (!list) return;
        try {
            var resp = await fetch('/api/characters/' + charId + '/skills');
            var skills = await resp.json();
            list.innerHTML = '';
            if (skills && skills.length > 0) {
                for (var s of skills) {
                    var item = document.createElement('div');
                    item.className = 'sk-item';
                    item.innerHTML = '<span>' + (s.icon || '⚡') + '</span><div><b>' + (s.name || '?') + '</b><small>' + (s.description || '') + '</small></div>';
                    list.appendChild(item);
                }
            } else {
                list.innerHTML = '<div class="sk-item"><span>📭</span><div><b>暂无技能</b><small>该角色没有加载任何技能</small></div></div>';
            }
        } catch (e) {
            console.error('加载技能列表失败:', e);
        }
    },

    /** 加载工作区文件列表 */
    loadWorkspace: async function(path, charId) {
        var page = document.querySelector('.sp-page[data-tab="workspace"] .ft');
        if (!page) return;
        var cId = charId || (AIGEME.chat.currentChar ? AIGEME.chat.currentChar.id : 'ario');
        try {
            var resp = await fetch('/api/workspace?path=' + encodeURIComponent(path || '') + '&character_id=' + encodeURIComponent(cId));
            var data = await resp.json();
            page.innerHTML = '';
            if (data.files) {
                // 非根目录：添加"返回上级"按钮
                if (data.path && data.path !== '.') {
                    var backEl = document.createElement('span');
                    backEl.className = 'ft-d';
                    backEl.style.cursor = 'pointer';
                    backEl.textContent = '📂 .. (返回上级)';
                    var parentPath = data.path.split('/').slice(0, -1).join('/');
                    backEl.onclick = function() {
                        AIGEME.loadWorkspace(parentPath || '', cId);
                    };
                    page.appendChild(backEl);
                }
                for (var f of data.files) {
                    var el = document.createElement('span');
                    el.className = f.type === 'dir' ? 'ft-d' : 'ft-f';
                    el.textContent = (f.type === 'dir' ? '📁 ' : '📄 ') + f.name;
                    if (f.type === 'dir') {
                        el.style.cursor = 'pointer';
                        el.onclick = function(name) {
                            return function() {
                                var newPath = data.path === '.' ? name : data.path + '/' + name;
                                AIGEME.loadWorkspace(newPath, cId);
                            };
                        }(f.name);
                    }
                    page.appendChild(el);
                }
            }
        } catch (e) {
            console.error('加载工作区失败:', e);
        }
    },

    /** 加载角色记忆到侧面板 */
    loadMemoryPanel: async function(charId) {
        var list = document.querySelector('#panel-right .memo-list');
        if (!list) return;
        try {
            var resp = await fetch('/api/characters/' + charId + '/memory');
            var data = await resp.json();
            if (data.index) {
                // 解析 MEMORY.md 的 section 摘要
                list.innerHTML = '';
                var lines = data.index.split('\n');
                var currentSection = '';
                for (var line of lines) {
                    line = line.trim();
                    if (line.startsWith('## ')) {
                        currentSection = line.substring(3);
                        var sectionEl = document.createElement('div');
                        sectionEl.className = 'memo-section';
                        sectionEl.textContent = currentSection;
                        list.appendChild(sectionEl);
                    } else if (line.startsWith('| ') && line.includes('|') && !line.includes('文件|')) {
                        var parts = line.split('|').map(function(p) { return p.trim(); });
                        if (parts.length >= 6 && parts[1]) {
                            var memo = document.createElement('div');
                            memo.className = 'memo';
                            memo.innerHTML = '<div class="memo-t">' + parts[1] + '</div><div class="memo-c">' + parts[5] + '</div>';
                            list.appendChild(memo);
                        }
                    }
                }
                if (list.children.length === 0) {
                    list.innerHTML = '<div class="memo"><div class="memo-c">暂无记忆</div></div>';
                }
            } else {
                list.innerHTML = '<div class="memo"><div class="memo-c">暂无记忆</div></div>';
            }
        } catch (e) {
            console.error('加载记忆失败:', e);
        }
    },
};

// 表情标签映射（用 var 避免与其他文件的 const 冲突）
var EMOJI_MAP = EMOJI_MAP || {
    '[Angry]': '😠', '[Happy]': '😊', '[Sad]': '😢',
    '[Smile]': '😄', '[Laugh]': '😂', '[Cry]': '😭',
    '[Love]': '😍', '[Shock]': '😱', '[Thinking]': '🤔',
    '[Confused]': '😕', '[Sleepy]': '😴', '[Wink]': '😉',
    '[Cool]': '😎', '[Blush]': '😊', '[Proud]': '😤',
    '[Joyful]': '🥳', '[Surprise]': '😮', '[Nervous]': '😰',
    '[Neutral]': '😐', '[Sigh]': '😮‍💨',
};

function replaceEmojiTags(text) {
    return text.replace(/\[(\w+)\]/g, (match, key) => {
        const bracketKey = '[' + key + ']';
        return EMOJI_MAP[bracketKey] || match;
    });
}

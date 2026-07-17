/**
 * UI 渲染模块 — 立绘、打字机、消息列表、思考面板
 * 仅操作设计稿 DOM（#ch-list / #sprite-img / #sprite-placeholder）
 */

const AIGEME_UI = {
    /** 渲染用户消息 — 追加到 ch-list */
    renderUserMessage: function(text) {
        var chList = document.getElementById('ch-list');
        if (!chList) return;

        var html = [
            '<div class="ch-msg ch-msg-user">',
            '  <div class="ch-msg-name">\u4F60</div>',
            '  <div class="ch-msg-text">', this._escapeHtml(replaceEmojiTags(text)), '</div>',
            '</div>'
        ].join('');
        chList.insertAdjacentHTML('beforeend', html);
        chList.scrollTop = chList.scrollHeight;
    },

    /** 渲染系统消息 — 追加到 ch-list */
    renderSystemMessage: function(text) {
        var chList = document.getElementById('ch-list');
        if (!chList) return;

        chList.insertAdjacentHTML('beforeend', [
            '<div class="ch-msg">',
            '  <div class="ch-msg-text" style="text-align:center;color:var(--text-hint);font-size:0.85rem;background:transparent;border:none;box-shadow:none;">',
            this._escapeHtml(replaceEmojiTags(text)),
            '  </div>',
            '</div>'
        ].join(''));
        chList.scrollTop = chList.scrollHeight;
    },

    /** 渲染旁白 — 追加到 ch-list */
    renderNarration: function(text) {
        var chList = document.getElementById('ch-list');
        if (!chList) return;

        chList.insertAdjacentHTML('beforeend', [
            '<div class="ch-msg">',
            '  <div class="ch-msg-text" style="font-style:italic;color:var(--text-hint);background:transparent;border:none;box-shadow:none;">',
            this._escapeHtml(replaceEmojiTags(text)),
            '  </div>',
            '</div>'
        ].join(''));
        chList.scrollTop = chList.scrollHeight;
    },

    /** 设置立绘表情 — 加载角色动态表情到 sprite-img */
    setTachie: function(expression) {
        var charId = AIGEME.chat.currentChar ? AIGEME.chat.currentChar.id : null;
        var placeholder = document.getElementById('sprite-placeholder');
        var spriteImg = document.getElementById('sprite-img');

        if (!charId) {
            // 无角色时显示 placeholder
            if (spriteImg) spriteImg.style.display = 'none';
            if (placeholder) placeholder.style.display = 'flex';
            return;
        }

        var expr = expression || 'default';
        // 从 expressions 映射中查实际文件名
        var exprMap = AIGEME.chat.currentChar.expressions || null;
        var actualFile = exprMap && exprMap[expr] ? exprMap[expr] : expr + '.png';
        // 取文件名不含 .png 的部分作为 URL 路径
        var imageName = actualFile.replace(/\.png$/i, '');
        // 优先使用 config.yaml 中的 tachie_dir，降级到 tachi-e/<charId>
        var tachieDir = (AIGEME.chat.currentChar.tachie_dir || ('tachi-e/' + charId)).replace(/\\/g, '/').replace(/\/$/, '');
        var src = '/' + tachieDir + '/' + imageName + '.png';

        // 默认表情文件名（用于 onerror 回退）
        var defaultFile = exprMap && exprMap['default'] ? exprMap['default'].replace(/\.png$/i, '') : 'default';
        var defaultSrc = '/' + tachieDir + '/' + defaultFile + '.png';

        // 设计稿元素：sprite-img（按角色动态加载）
        if (spriteImg) {
            spriteImg.src = src;
            spriteImg.style.display = '';  // 确保可见（HTML 中默认为 display:none）
            spriteImg.onerror = function() {
                this.src = defaultSrc;
            };
            // 隐藏 placeholder
            if (placeholder) placeholder.style.display = 'none';
            // 图片加载后调整 3:4 比例
            spriteImg.onload = function() {
                AIGEME.adjustSpriteRatio();
            };
        }
    },

    /** 打字机效果 — 逐字显示文本（支持 innerHTML 模式） */
    typewriterEffect: function(element, text, useMdRender) {
        var speed = AIGEME.chat.typewriter.speed;
        AIGEME.chat.typewriter.isTyping = true;
        // 快照当前 turnId，打字机执行中若 turnId 变化则自动失效
        var myTurnId = AIGEME.chat.turnId;

        function _isCurrent() {
            return AIGEME.chat.turnId === myTurnId;
        }

        // 长文本跳过逐字显示，直接渲染
        if (text.length > 200) {
            if (!_isCurrent()) { AIGEME.chat.typewriter.isTyping = false; return; }
            if (useMdRender && window.mdRender) {
                element.innerHTML = window.mdRender(text);
            } else {
                element.innerHTML = text;
            }
            AIGEME.chat.typewriter.isTyping = false;
            var cursor = element.querySelector('.cursor-blink');
            if (cursor) cursor.remove();
            var chatText = document.getElementById('chat-text');
            if (chatText && chatText.innerHTML !== element.innerHTML) {
                chatText.innerHTML = element.innerHTML;
            }
            if (AIGEME.chat.turnEnded) {
                AIGEME.finishStream(AIGEME.chat.turnCancelled);
                _refreshHistoryIfOpen();
            }
            return;
        }

        var index = 0;
        element.innerHTML = '';

        function type() {
            // turnId 已变化（切角色/新对话），立即中止
            if (!_isCurrent()) {
                AIGEME.chat.typewriter.isTyping = false;
                return;
            }
            if (index < text.length && AIGEME.chat.typewriter.isTyping) {
                element.innerHTML += text.charAt(index);
                index++;
                setTimeout(type, speed);
            } else {
                AIGEME.chat.typewriter.isTyping = false;

                // 打字完成：如果启用 MD 渲染，替换为渲染后的 HTML
                if (useMdRender && window.mdRender) {
                    element.innerHTML = window.mdRender(text);
                }
                var cursor = element.querySelector('.cursor-blink');
                if (cursor) cursor.remove();

                // 同步更新到 chat-text（仅当 turnId 仍然有效时）
                if (_isCurrent()) {
                    var chatText = document.getElementById('chat-text');
                    if (chatText && chatText.innerHTML !== element.innerHTML) {
                        chatText.innerHTML = element.innerHTML;
                    }

                    // 如果 turn_end 已到，此时才真正 finishStream
                    if (AIGEME.chat.turnEnded) {
                        AIGEME.finishStream(AIGEME.chat.turnCancelled);
                        // ★ 修复：打字机完成后刷新历史列表（如果面板已打开）
                        _refreshHistoryIfOpen();
                    }
                }
            }
        }

        // 添加光标
        var cursor = document.createElement('span');
        cursor.className = 'cursor-blink';
        cursor.textContent = '|';
        element.appendChild(cursor);

        type();
    },

    /** HTML 转义 */
    _escapeHtml: function(text) {
        if (!text) return '';
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /** 渲染历史会话列表 */
    renderHistoryPanel: function(conversations, currentDate) {
        var list = document.getElementById('history-list');
        if (!list) return;

        list.innerHTML = '';

        // 找到当前选中的对话（currentDate），没有则取最新一条
        var currentConv = null;
        var otherConvs = [];
        if (conversations && conversations.length > 0) {
            for (var i = 0; i < conversations.length; i++) {
                if (currentDate && conversations[i].date === currentDate) {
                    currentConv = conversations[i];
                } else {
                    otherConvs.push(conversations[i]);
                }
            }
            if (!currentConv) {
                currentConv = conversations[0];
                otherConvs = conversations.slice(1);
            }
        }

        // ── 当前对话（用户选中/正在浏览的会话） ──
        var currentSection = document.createElement('div');
        currentSection.className = 'history-section';
        currentSection.innerHTML = '<div class="history-section-title">📝 当前对话</div>';
        list.appendChild(currentSection);

        if (currentConv) {
            var currentItem = document.createElement('div');
            currentItem.className = 'history-item current';
            currentItem.innerHTML = [
                '<div class="history-item-date" style="color:var(--accent);">', currentConv.date, '</div>',
                '<div class="history-item-preview">', this._escapeHtml(currentConv.last_message || ''), '</div>',
                '<div class="history-item-count">', currentConv.message_count, ' 条消息</div>'
            ].join('');
            currentItem.onclick = (function(conv) {
                return function() {
                    if (AIGEME.chat.currentChar) {
                        AIGEME.loadConversation(AIGEME.chat.currentChar.id, conv.date);
                    }
                };
            })(currentConv);
            list.appendChild(currentItem);
        } else {
            var emptyCurrent = document.createElement('div');
            emptyCurrent.className = 'history-empty';
            emptyCurrent.textContent = '暂无对话';
            list.appendChild(emptyCurrent);
        }

        // ── 开始新对话 ──
        var newSection = document.createElement('div');
        newSection.className = 'history-section';
        newSection.innerHTML = '<div class="history-section-title">✨ 开始新对话</div>';
        list.appendChild(newSection);

        var newItem = document.createElement('div');
        newItem.className = 'history-item new-chat';
        newItem.style.cursor = 'pointer';
        newItem.innerHTML = '<div class="history-item-date" style="color:var(--accent); text-align:center; padding:0.5rem;">＋ 开启新会话</div>';
        newItem.onclick = function() {
            AIGEME.startNewChat();
        };
        list.appendChild(newItem);

        // ── 历史对话（排除当前选中的其他会话） ──
        if (otherConvs.length === 0) {
            var empty = document.createElement('div');
            empty.className = 'history-empty';
            empty.textContent = '暂无历史对话';
            list.appendChild(empty);
            return;
        }

        var historySection = document.createElement('div');
        historySection.className = 'history-section';
        historySection.innerHTML = '<div class="history-section-title">📜 历史对话</div>';
        list.appendChild(historySection);

        for (var i = 0; i < otherConvs.length; i++) {
            var conv = otherConvs[i];
            var item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = [
                '<div class="history-item-date">', conv.date, '</div>',
                '<div class="history-item-preview">', this._escapeHtml(conv.last_message || ''), '</div>',
                '<div class="history-item-count">', conv.message_count, ' 条消息</div>'
            ].join('');
            item.onclick = (function(conv) {
                return function() {
                    if (AIGEME.chat.currentChar) {
                        AIGEME.loadConversation(AIGEME.chat.currentChar.id, conv.date);
                    }
                };
            })(conv);
            list.appendChild(item);
        }
    },

    /** 加载历史对话到消息列表 */
    loadHistory: function(charId, date, records) {
        var chList = document.getElementById('ch-list');
        if (!chList) return;

        // 清空当前状态（不主动断开 WS — 由调用方决定是否断连）
        AIGEME.chat.dialogue = [];
        AIGEME.chat.currentMessage = '';
        AIGEME.chat.isStreaming = false;
        AIGEME.chat.typewriter.isTyping = false;
        chList.innerHTML = '';

        // 关闭历史面板
        document.getElementById('chat-history')?.classList.remove('open');

        // 渲染每条记录
        for (var i = 0; i < records.length; i++) {
            var rec = records[i];
            var data = rec.data || {};
            var role = data.role;
            var content = data.content || '';
            // 跳过 tool 消息和 content="" 的 assistant 消息（它们是 tool call 标记，不是对话内容）
            if (role === 'tool') continue;
            if (role === 'assistant' && !content) continue;
            if (role === 'user') {
                this.renderUserMessage(content);
            } else if (role === 'assistant') {
                var displayContent = stripTtsTags(content);
                var ttsTurnId = (rec.meta && rec.meta.tts_turn_id) || '';
                chList.insertAdjacentHTML('beforeend', [
                    '<div class="ch-msg ch-msg-assistant" data-tts-turn-id="' + ttsTurnId + '">',
                    '  <div class="ch-msg-name">', AIGEME.chat.currentChar ? AIGEME.chat.currentChar.name : '',
                    (ttsTurnId ? '    <button class="tts-replay-btn" data-tts-turn-id="' + ttsTurnId + '" title="重播语音">🔁</button>' : ''),
                    '</div>',
                    '  <div class="ch-msg-text">', this._escapeHtml(replaceEmojiTags(displayContent)), '</div>',
                    '</div>'
                ].join(''));
                AIGEME.chat.dialogue.push({ role: role, content: content, tts_turn_id: ttsTurnId });
            } else {
                // 非 assistant/user 消息也存储到 dialogue（保持完整性）
                AIGEME.chat.dialogue.push({ role: role, content: content });
            }
        }
    },
};

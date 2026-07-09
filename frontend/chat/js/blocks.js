/**
 * Block 渲染器 — 按 block_type 分派到不同的渲染函数
 */
const BlockRenderer = {
    /** HTML 转义 */
    _escapeHtml: function(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /** CSS 属性选择器值转义（防止 tool_call_id 含特殊字符导致选择器失效） */
    _escapeAttr: function(text) {
        return String(text).replace(/["\\]/g, '\\$&');
    },

    /** 处理接收到的 Block */
    handle: function(block) {
        // 优先处理 plan 相关 block
        if (['plan_thinking', 'plan', 'plan_progress', 'plan_review'].includes(block.block_type)) {
            this._handlePlanBlock(block);
            return;
        }
        const handler = this._getHandler(block.block_type);
        if (handler) {
            handler(block);
        } else {
            console.warn('未知 Block 类型:', block.block_type);
        }
    },

    /** 获取对应类型的处理函数 */
    _getHandler: function(blockType) {
        const handlers = {
            'thinking': this._handleThinking,
            'speech': this._handleSpeech,
            'expression': this._handleExpression,
            'tool_call': this._handleToolCall,
            'tool_result': this._handleToolResult,
            'turn_end': this._handleTurnEnd,
            'error': this._handleError,
            'system': this._handleSystem,
            'narration': this._handleNarration,
            'scene': this._handleScene,
            'choice': this._handleChoice,
            'bgm': this._handleBgm,
            'emotion': this._handleEmotion,
            'confirm': this._handleConfirm,
            'memory_update': this._handleMemoryUpdate,
            'workspace_update': this._handleWorkspaceUpdate,
            'audio': this._handleAudio,
        };
        return handlers[blockType] ? handlers[blockType].bind(this) : null;
    },

    /** plan 相关 Block 分发处理 */
    _planRenderers: new Map(), // containerEl → PlanCardRenderer 实例

    _handlePlanBlock: function(block) {
        // plan 卡片渲染到 chat-box（始终可见），不依赖 .ai-message
        let container = document.querySelector('#chat-box > .plan-container');

        // 需要新建容器的条件：来了新的 plan_thinking，且上一个已完成/失败
        if (!container) {
            // 没有容器 → 建新
            container = document.createElement('div');
            container.className = 'plan-container';
            const chatBox = document.getElementById('chat-box');
            if (!chatBox) return;
            const chatText = document.getElementById('chat-text');
            if (chatText) {
                chatBox.insertBefore(container, chatText);
            } else {
                chatBox.appendChild(container);
            }
            this._planRenderers.set(container, new window.PlanCardRenderer(container));
        } else if (block.block_type === 'plan_thinking') {
            // 已有容器但已结束，来了新计划 → 重建
            const renderer = this._planRenderers.get(container);
            if (renderer && (renderer.state === 'COMPLETED' || renderer.state === 'FAILED')) {
                const newContainer = document.createElement('div');
                newContainer.className = 'plan-container';
                container.parentNode.insertBefore(newContainer, container.nextSibling);
                container = newContainer;
                this._planRenderers.set(container, new window.PlanCardRenderer(container));
            }
        }

        const renderer = this._planRenderers.get(container);
        if (renderer) {
            renderer.handleBlock(block);
        }
    },

    /** thinking — 思考面板（同时更新设计稿 think-panel 和旧 thinking-panel 回退） */
    _handleThinking: function(block) {
        const thinkPanel = document.getElementById('think-panel');
        const thinkContent = document.getElementById('think-content');
        const oldPanel = document.getElementById('thinking-panel');
        const oldText = document.getElementById('thinking-text');

        // 显示设计稿面板
        if (thinkPanel) {
            thinkPanel.classList.add('visible');
            AIGEME.chat.thinkPanelVisible = true;
            const btnThink = document.getElementById('btn-think');
            if (btnThink) btnThink.classList.add('active');
        }

        if (thinkContent) {
            if (block.is_final) {
                // 最终标记，不变
            } else if (block.delta) {
                if (!AIGEME.chat.thinkingAppended) {
                    thinkContent.innerHTML = '';
                    AIGEME.chat.thinkingAppended = true;
                }
                // 修复: 追加到当前行末尾，不另起新行（避免逐字换行）
                var lastLine = thinkContent.querySelector('.thinking-line:last-child');
                if (lastLine) {
                    lastLine.innerHTML += this._escapeHtml(block.delta);
                } else {
                    thinkContent.innerHTML += '<div class="thinking-line">💭 ' + this._escapeHtml(block.delta) + '</div>';
                }
            }
            const body = thinkContent.parentElement;
            if (body) body.scrollTop = body.scrollHeight;
        }

        // 旧面板回退
        if (oldPanel && oldText) {
            oldPanel.classList.remove('hidden');
            if (!block.is_final && block.delta) {
                if (!AIGEME.chat.thinkingAppended) {
                    oldText.textContent = '';
                }
                oldText.textContent += block.delta;
            }
            AIGEME.chat.thinking = thinkContent ? thinkContent.textContent : (block.delta || '');
            AIGEME.chat.thinkingVisible = true;
        }
        AIGEME._updateStopButton();
    },

    /** speech — 打字机输出（使用设计稿的 chat-text + chat-speaker，旧 current-output 回退） */
    _handleSpeech: function(block) {
        const chatText = document.getElementById('chat-text');
        const chatSpeaker = document.getElementById('chat-speaker');
        const output = document.getElementById('current-output');
        const textEl = document.getElementById('output-text');
        const labelEl = document.getElementById('output-char-label');

        if (!chatText && !textEl) return;

        AIGEME.chat.isStreaming = true;

        if (chatSpeaker && AIGEME.chat.currentChar) {
            chatSpeaker.textContent = AIGEME.chat.currentChar.name;
        }

        // 流式块（非最终）— 每个 delta 都要检查 turnId，防止切角色后旧WS消息污染新界面
        if (!block.is_final && block.delta) {
            // 若 turnId 已变化（切角色/新对话），丢弃此 block
            if (typeof AIGEME.chat.turnId !== 'undefined' && AIGEME.chat._speechTurnId !== undefined
                && AIGEME.chat._speechTurnId !== AIGEME.chat.turnId) {
                return;
            }
            const cleanDelta = replaceEmojiTags(block.delta);
            if (chatText) chatText.innerHTML += cleanDelta;
            if (textEl) textEl.innerHTML += cleanDelta;
            AIGEME.appendStream(block.delta);
            return;
        }

        if (block.is_final && !block.delta) {
            AIGEME._updateStopButton();
            return;
        }

        // is_final=true 且有 delta（整段输出，走打字机）— 记录当前 turnId 供后续流式 delta 检查
        AIGEME.chat._speechTurnId = AIGEME.chat.turnId;
        AIGEME.appendStream(block.delta);
        if (textEl) {
            const cleanText = replaceEmojiTags(block.delta);
            AIGEME_UI.typewriterEffect(textEl, cleanText, true);
        }
        AIGEME._updateStopButton();
    },

    /** expression — 立绘切换 */
    _handleExpression: function(block) {
        AIGEME.setExpression(block.delta);
        AIGEME_UI.setTachie(block.delta);
        if (typeof ShakeManager !== 'undefined') {
            var spriteImg = document.getElementById('sprite-img');
            if (spriteImg) {
                ShakeManager.shake(spriteImg, block.delta);
            }
        }
    },

    /** memory_update — 记忆已更新，刷新右侧面板 */
    _handleMemoryUpdate: function(block) {
        if (AIGEME.chat.currentChar) {
            console.log('[memory_update] 刷新记忆面板', AIGEME.chat.currentChar.id);
            AIGEME.loadMemoryPanel(AIGEME.chat.currentChar.id);
        }
    },

    /** workspace_update — 工作区已更新，刷新左侧面板 */
    _handleWorkspaceUpdate: function(block) {
        if (AIGEME.chat.currentChar) {
            AIGEME.loadWorkspace('', AIGEME.chat.currentChar.id);
        }
    },

    /** tool_call — 工具调用通知（在设计稿 tool-calls 中创建卡片，旧 tool-status 回退） */
    _handleToolCall: function(block) {
        const toolCalls = document.getElementById('tool-calls');
        if (toolCalls) {
            // 确保面板可见
            const panel = document.getElementById('think-panel');
            if (panel) {
                panel.classList.add('visible');
                AIGEME.chat.thinkPanelVisible = true;
                const btnThink = document.getElementById('btn-think');
                if (btnThink) btnThink.classList.add('active');
            }

            const name = block.delta || 'unknown';
            const args = (block.metadata && block.metadata.args) || {};
            const argsStr = JSON.stringify(args, null, 2);
            var callIdx = AIGEME.chat.toolCallCount++;
            var toolCallId = (block.metadata && block.metadata.tool_call_id) || '';

            const item = document.createElement('div');
            item.className = 'tool-call-item';
            item.dataset.tool = name;
            item.dataset.callIndex = callIdx;
            if (toolCallId) item.dataset.toolCallId = toolCallId;
            item.innerHTML =
                '<div class="tool-call-header">' +
                    '<span class="tool-call-icon">🔧</span>' +
                    '<span class="tool-call-name">' + name + '</span>' +
                    '<span class="tool-call-status running">处理中...</span>' +
                '</div>' +
                '<div class="tool-call-args">' + this._escapeHtml(argsStr) + '</div>' +
                '<div class="tool-call-output" style="display:none;"></div>';
            toolCalls.appendChild(item);
            toolCalls.scrollTop = toolCalls.scrollHeight;
        }

        if (window.soundManager) {
            window.soundManager.play('tool_call');
        }

        // 旧回退
        const status = document.getElementById('tool-status');
        const text = document.getElementById('tool-text');
        if (status && text) {
            status.classList.remove('hidden');
            text.textContent = '正在使用 ' + (block.delta || '') + '...';
        }
    },

    /** tool_result — 工具结果（更新 tool-call-item 状态，旧 tool-status 回退） */
    _handleToolResult: function(block) {
        // 优先按 metadata.tool_call_id 精确匹配
        var targetItem = null;
        var blockToolCallId = (block.metadata && block.metadata.tool_call_id) || '';
        if (blockToolCallId) {
            targetItem = document.querySelector('.tool-call-item[data-tool-call-id="' + this._escapeAttr(blockToolCallId) + '"]');
        }
        if (!targetItem) {
            // 按 tool name 匹配
            if (block.delta) {
                var allItems = document.querySelectorAll('.tool-call-item');
                for (var i = allItems.length - 1; i >= 0; i--) {
                    var st = allItems[i].querySelector('.tool-call-status');
                    if (st && st.classList.contains('running')) {
                        targetItem = allItems[i];
                        break;
                    }
                }
            }
            if (!targetItem) {
                var items = document.querySelectorAll('.tool-call-item');
                if (items.length > 0) targetItem = items[items.length - 1];
            }
        }

        if (targetItem) {
            const statusEl = targetItem.querySelector('.tool-call-status');
            const outputEl = targetItem.querySelector('.tool-call-output');
            if (statusEl) {
                statusEl.className = 'tool-call-status done';
                statusEl.textContent = '完成 ✨';
            }
            if (outputEl && block.delta) {
                outputEl.style.display = 'block';
                outputEl.textContent = block.delta;
            }
        }

        const status = document.getElementById('tool-status');
        if (status) {
            status.classList.add('hidden');
        }
    },

    /** turn_end — 本轮结束 */
    _handleTurnEnd: function(block) {
        // 不隐藏设计稿的 think-panel（用户可手动关闭）
        document.getElementById('tool-status')?.classList.add('hidden');

        // ★ 兜底：将所有仍为 running 状态的工具卡片标记为完成
        // （防止 tool_result block 因网络/序列化问题丢失导致卡片永远卡在"处理中"）
        var runningCards = document.querySelectorAll('.tool-call-status.running');
        for (var i = 0; i < runningCards.length; i++) {
            runningCards[i].className = 'tool-call-status done';
            runningCards[i].textContent = '完成';
        }

        const cancelled = block.metadata && block.metadata.cancelled;
        AIGEME.chat.isStreaming = false;
        AIGEME.chat.turnEnded = true;
        AIGEME.chat.turnCancelled = !!cancelled;
        AIGEME.chat.thinkingAppended = false;

        if (window.soundManager) {
            window.soundManager.play('turn_end');
        }

        if (!AIGEME.chat.typewriter.isTyping) {
            AIGEME.finishStream(!!cancelled);
        }

        if (cancelled) {
            AIGEME_UI.renderSystemMessage('已取消');
        }

        // 解锁输入
        const inputMsg = document.getElementById('input-msg');
        const userInput = document.getElementById('user-input');
        if (inputMsg) inputMsg.disabled = false;
        if (userInput) userInput.disabled = false;

        AIGEME._updateStopButton();

        // ★ 修复：对话结束后自动刷新历史会话列表（如果历史面板已打开）
        _refreshHistoryIfOpen();
    },

    /** error — 错误信息 */
    _handleError: function(block) {
        console.error('服务器错误:', block.delta);
        AIGEME_UI.renderSystemMessage('错误: ' + block.delta);
        if (window.soundManager) {
            window.soundManager.play('error');
        }
    },

    /** system — 系统消息 */
    _handleSystem: function(block) {
        var delta = block.delta || '';
        // 提取 session_id（用于 HTTP 确认端点）
        if (delta.indexOf('session_id:') === 0) {
            if (window.AIGEME && AIGEME.shared) {
                AIGEME.shared.sessionId = delta.substring(11).trim();
            }
            return; // 不渲染到聊天界面
        }
        AIGEME_UI.renderSystemMessage(delta);
    },

    /** narration — 旁白 */
    _handleNarration: function(block) {
        AIGEME_UI.renderNarration(block.delta);
    },

    /** audio — TTS 合成音频 */
    _handleAudio: function(block) {
        if (typeof TTSPlayer === 'undefined') return;
        var index = (block.metadata && block.metadata.index) || 0;
        TTSPlayer.play(block.delta, index);
    },

    /** scene — 场景切换（预留） */
    _handleScene: function(block) {
        // Phase 2 实现
    },

    /** choice — 选项分支（预留） */
    _handleChoice: function(block) {
        // Phase 2 实现
    },

    /** bgm — 背景音乐（预留） */
    _handleBgm: function(block) {
        // Phase 2 实现
    },

    /** emotion — 情感状态（预留） */
    _handleEmotion: function(block) {
        // Phase 2 实现
    },

    /** confirm — 确认对话框 */
    _handleConfirm: function(block) {
        var message = block.delta || '确认此操作?';
        var overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.innerHTML =
            '<div class="confirm-dialog">' +
                '<div class="confirm-icon">&#x26A0;&#xFE0F;</div>' +
                '<div class="confirm-message">' + this._escapeHtml(message) + '</div>' +
                '<div class="confirm-actions">' +
                    '<button class="confirm-btn cancel">取消</button>' +
                    '<button class="confirm-btn confirm">确认</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(overlay);

        // 自动聚焦确认按钮
        var confirmBtn = overlay.querySelector('.confirm-btn.confirm');
        var cancelBtn = overlay.querySelector('.confirm-btn.cancel');
        confirmBtn.focus();

        // 键盘快捷键
        var keyHandler = function(e) {
            if (e.key === 'Enter') { confirmBtn.click(); }
            if (e.key === 'Escape') { cancelBtn.click(); }
        };
        document.addEventListener('keydown', keyHandler);

        // 渐入动画
        requestAnimationFrame(function() { overlay.classList.add('visible'); });

        // 清理
        var cleanup = function() {
            overlay.classList.remove('visible');
            document.removeEventListener('keydown', keyHandler);
            setTimeout(function() { overlay.remove(); }, 200);
        };

        confirmBtn.onclick = function() {
            // 从 block metadata 中取 session_id 发送确认
            var sid = (block.metadata && block.metadata.session_id) || (window.AIGEME && AIGEME.shared && AIGEME.shared.sessionId) || '';
            if (sid) {
                var xhr = new XMLHttpRequest();
                xhr.open('POST', '/api/confirm?session_id=' + encodeURIComponent(sid) + '&action=confirm', true);
                xhr.send();
            }
            cleanup();
        };
        cancelBtn.onclick = function() {
            var sid = (block.metadata && block.metadata.session_id) || (window.AIGEME && AIGEME.shared && AIGEME.shared.sessionId) || '';
            if (sid) {
                var xhr = new XMLHttpRequest();
                xhr.open('POST', '/api/confirm?session_id=' + encodeURIComponent(sid) + '&action=cancel', true);
                xhr.send();
            }
            cleanup();
        };
        // 点击遮罩层取消
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) cancelBtn.click();
        });
    },
};

/** 如果历史面板（侧面板 → 历史 tab）已打开，自动刷新会话列表 */
function _refreshHistoryIfOpen() {
    var panelLeft = document.getElementById('panel-left');
    var historyTab = document.querySelector('.sp-page[data-tab="history"]');
    if (panelLeft && panelLeft.classList.contains('open') &&
        historyTab && historyTab.classList.contains('active') &&
        AIGEME.chat.currentChar) {
        AIGEME.loadConversations(AIGEME.chat.currentChar.id);
    }
}

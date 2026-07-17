/**
 * AIGEME 前端主入口 — 初始化 + 事件绑定
 * 兼容设计稿新 DOM + 旧 JS 回退
 */
(function() {
    'use strict';

    // 页面加载完成后初始化
    document.addEventListener('DOMContentLoaded', function() {
        console.log('AIGEME 前端 v0.1.0 已加载（设计稿 Phase 1）');

        // 初始化音效管理器
        window.soundManager = new SoundManager();

        // ==========================================
        // 1. data-go 导航按钮 — 所有带 data-go 属性的按钮自动绑定
        // ==========================================
        document.querySelectorAll('[data-go]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                if (window.soundManager) window.soundManager.play('button_click');
                var target = this.getAttribute('data-go');
                if (target === 'screen-chat') {
                    // 修复: "开始聊天"按钮 — 检查是否有最近角色
                    AIGEME.handleStartChat();
                } else if (target) {
                    AIGEME.setScreen(target);
                }
            });
        });

        // ==========================================
        // 2. 输入框 — 兼容新旧（user-input / input-msg）
        // ==========================================
        var userInput = document.getElementById('user-input');
        var inputMsg = document.getElementById('input-msg');

        // 设计稿输入框：绑定回车发送
        if (userInput) {
            userInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    AIGEME.sendMessage();
                    // 重置高度
                    this.style.height = 'auto';
                }
            });
            // 自动调整高度
            userInput.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 100) + 'px';
            });
        }

        // 旧输入框：绑定回车发送
        if (inputMsg) {
            inputMsg.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    AIGEME.sendMessage();
                }
            });
        }

        // 同步两个输入框的内容（当一个输入框变化时同步另一个）
        if (userInput && inputMsg) {
            userInput.addEventListener('input', function() {
                inputMsg.value = this.value;
            });
            inputMsg.addEventListener('input', function() {
                userInput.value = this.value;
            });
        }

        // ==========================================
        // 3. 发送/停止按钮 — 合一（send-btn 内含 icon-send 和 icon-stop）
        // ==========================================
        var btnSend = document.getElementById('send-btn');
        if (btnSend) {
            btnSend.addEventListener('click', function() {
                if (AIGEME.chat.isStreaming || AIGEME.chat.typewriter.isTyping) {
                    AIGEME.cancelStream();
                } else {
                    AIGEME.sendMessage();
                }
            });
        }

        // 旧风格独立停止按钮（保留兼容）
        var stopBtn = document.getElementById('stop-btn');
        if (stopBtn) {
            stopBtn.addEventListener('click', function() {
                AIGEME.cancelStream();
            });
        }

        // ==========================================
        // 4. 消息复制功能 — 右键菜单
        // ==========================================
        document.addEventListener('contextmenu', function(e) {
            var msgItem = e.target.closest('.message-item') || e.target.closest('.ch-msg');
            if (msgItem) {
                e.preventDefault();
                var bubble = msgItem.querySelector('.message-bubble') || msgItem.querySelector('.ch-msg-text');
                if (bubble) {
                    var text = bubble.textContent || bubble.innerText;
                    navigator.clipboard.writeText(text).then(function() {
                        var tip = document.createElement('div');
                        tip.className = 'copy-tip';
                        tip.textContent = '\u5DF2\u590D\u5236';
                        msgItem.appendChild(tip);
                        setTimeout(function() { tip.remove(); }, 1500);
                    }).catch(function() {
                        console.warn('复制失败');
                    });
                }
            }
        });

        // 消息复制 — 长按（移动端）
        var pressTimer = null;
        document.addEventListener('touchstart', function(e) {
            var msgItem = e.target.closest('.message-item') || e.target.closest('.ch-msg');
            if (msgItem) {
                pressTimer = setTimeout(function() {
                    var bubble = msgItem.querySelector('.message-bubble') || msgItem.querySelector('.ch-msg-text');
                    if (bubble) {
                        var text = bubble.textContent || bubble.innerText;
                        navigator.clipboard.writeText(text).then(function() {
                            var tip = document.createElement('div');
                            tip.className = 'copy-tip';
                            tip.textContent = '\u5DF2\u590D\u5236';
                            msgItem.appendChild(tip);
                            setTimeout(function() { tip.remove(); }, 1500);
                        });
                    }
                }, 500);
            }
        }, { passive: true });
        document.addEventListener('touchend', function() {
            if (pressTimer) {
                clearTimeout(pressTimer);
                pressTimer = null;
            }
        }, { passive: true });

        // ==========================================
        // 5. 设计稿 think-panel 控制
        // ==========================================
        // 关闭按钮
        var thinkClose = document.getElementById('think-close');
        if (thinkClose) {
            thinkClose.addEventListener('click', function() {
                var panel = document.getElementById('think-panel');
                if (panel) {
                    panel.classList.remove('visible');
                    AIGEME.chat.thinkPanelVisible = false;
                    var btnThink = document.getElementById('btn-think');
                    if (btnThink) btnThink.classList.remove('active');
                }
            });
        }

        // btn-think 切换面板
        var btnThink = document.getElementById('btn-think');
        if (btnThink) {
            btnThink.addEventListener('click', function() {
                var panel = document.getElementById('think-panel');
                if (panel) {
                    panel.classList.toggle('visible');
                    AIGEME.chat.thinkPanelVisible = panel.classList.contains('visible');
                    btnThink.classList.toggle('active');
                }
            });
        }

        // think-panel 拖拽
        var thinkHandle = document.getElementById('think-handle');
        var thinkPanel = document.getElementById('think-panel');
        if (thinkHandle && thinkPanel) {
            var isDragging = false;
            var startX, startY, offsetX, offsetY;

            thinkHandle.addEventListener('mousedown', function(e) {
                isDragging = true;
                startX = e.clientX;
                startY = e.clientY;
                offsetX = thinkPanel.offsetLeft;
                offsetY = thinkPanel.offsetTop;
                thinkPanel.style.cursor = 'grabbing';
                e.preventDefault();
            });

            document.addEventListener('mousemove', function(e) {
                if (!isDragging) return;
                var dx = e.clientX - startX;
                var dy = e.clientY - startY;
                thinkPanel.style.left = (thinkPanel.offsetLeft > 0 ? thinkPanel.offsetLeft : null) + 'px';
                thinkPanel.style.right = null;
                thinkPanel.style.left = (offsetX + dx) + 'px';
                thinkPanel.style.top = Math.max(0, offsetY + dy) + 'px';
            });

            document.addEventListener('mouseup', function() {
                if (isDragging) {
                    isDragging = false;
                    thinkPanel.style.cursor = '';
                }
            });
        }

        // ==========================================
        // 6. 设计稿侧面板控制
        // ==========================================
        var btnPanel = document.getElementById('btn-panel');
        if (btnPanel) {
            btnPanel.addEventListener('click', function() {
                AIGEME.togglePanel('left');
            });
        }

        var btnMem = document.getElementById('btn-mem');
        if (btnMem) {
            btnMem.addEventListener('click', function() {
                AIGEME.togglePanel('right');
            });
        }

        // 侧面板关闭按钮
        document.querySelectorAll('.sp-close').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var id = this.getAttribute('data-close');
                var panel = document.getElementById(id);
                if (panel) panel.classList.remove('open');
            });
        });

        // ==========================================
        // 7. 设计稿侧面板 tab 切换
        // ==========================================
        document.querySelectorAll('.sp-tab').forEach(function(tab) {
            tab.addEventListener('click', function() {
                var parent = this.closest('.side-panel');
                if (!parent) return;
                parent.querySelectorAll('.sp-tab').forEach(function(t) { t.classList.remove('active'); });
                this.classList.add('active');
                var tabName = this.getAttribute('data-tab');
                parent.querySelectorAll('.sp-page').forEach(function(p) {
                    p.classList.toggle('active', p.getAttribute('data-tab') === tabName);
                });
                // 修复: 切换到历史 tab 时加载历史会话列表
                if (tabName === 'history' && AIGEME.chat.currentChar) {
                    AIGEME.loadConversations(AIGEME.chat.currentChar.id);
                }
                // 动态加载面板内容
                if (tabName === 'workspace' && AIGEME.chat.currentChar) {
                    AIGEME.loadWorkspace('', AIGEME.chat.currentChar.id);
                }
                if (tabName === 'skills' && AIGEME.chat.currentChar) {
                    AIGEME.loadSkills(AIGEME.chat.currentChar.id);
                }
            });
        });

        // ==========================================
        // 8. 设计稿历史记录展开/收起
        // ==========================================
        var expandBtn = document.getElementById('expand-btn');
        if (expandBtn) {
            expandBtn.addEventListener('click', function() {
                var ch = document.getElementById('chat-history');
                if (ch) ch.classList.toggle('open');
            });
        }

        var chCollapse = document.getElementById('ch-collapse');
        if (chCollapse) {
            chCollapse.addEventListener('click', function() {
                document.getElementById('chat-history')?.classList.remove('open');
            });
        }

        // ==========================================
        // 9. 文件上传 + 图片处理
        // ==========================================
        var fileBtn = document.getElementById('file-btn');
        var fileInput = document.getElementById('file-input');
        if (fileBtn && fileInput) {
            // 允许图片选择
            fileInput.setAttribute('accept', 'image/*');
            fileBtn.addEventListener('click', function() {
                fileInput.click();
            });
            fileInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    // 处理选中的图片文件
                    for (var fi = 0; fi < this.files.length; fi++) {
                        var file = this.files[fi];
                        if (file.type && file.type.startsWith('image/')) {
                            if (file.size > 5 * 1024 * 1024) {  // 5MB 限制
                                alert('图片大小不能超过 5MB');
                                continue;
                            }
                            var reader = new FileReader();
                            reader.onload = function(e) {
                                var b64 = e.target.result.split(',')[1];
                                AIGEME._addImage(b64);
                            };
                            reader.readAsDataURL(file);
                        }
                    }
                    fileBtn.classList.add('has-file');
                    console.log('已选择图片:', this.files.length);
                }
            });
        }

        // ==========================================
        // 9b. 粘贴图片
        // ==========================================
        var inputEl = document.getElementById('user-input') || document.getElementById('input-msg');
        if (inputEl) {
            inputEl.addEventListener('paste', function(e) {
                var items = e.clipboardData && e.clipboardData.items;
                if (!items) return;
                for (var pi = 0; pi < items.length; pi++) {
                    var item = items[pi];
                    if (item.type && item.type.startsWith('image/')) {
                        var blob = item.getAsFile();
                        if (blob) {
                            if (blob.size > 5 * 1024 * 1024) {  // 5MB 限制
                                alert('图片大小不能超过 5MB');
                                continue;
                            }
                            var reader = new FileReader();
                            reader.onload = function(ev) {
                                var b64 = ev.target.result.split(',')[1];
                                AIGEME._addImage(b64);
                            };
                            reader.readAsDataURL(blob);
                        }
                    }
                }
            });
        }

        // ==========================================
        // 10. 设置页 — 加载/保存模型设置
        // ==========================================
        var settingProvider = document.getElementById('setting-provider');
        var settingModelName = document.getElementById('setting-model-name');
        var modelPickHint = document.getElementById('model-pick-hint');
        // 缓存的 provider 列表（由 /api/llm-providers 加载）
        var providerList = [];
        var providersLoaded = false;
        var settingTempSlider = document.getElementById('setting-temp-slider');
        var settingTempInput = document.getElementById('setting-temp-input');
        var settingApiBase = document.getElementById('setting-api-base');
        var settingMaxTokens = document.getElementById('setting-max-tokens');
        var settingApiKey = document.getElementById('setting-api-key');
        var apiKeyStatus = document.getElementById('api-key-status');
        var apiKeyClear = document.getElementById('api-key-clear');
        // 标记是否已有已保存的 key（加载后由后端 has_api_key 决定）
        var apiKeyConfigured = false;
        var settingPreserveThinking = document.getElementById('setting-preserve-thinking');
        var ptStatus = document.getElementById('pt-status');
        var settingCtxSlider = document.getElementById('setting-ctx-slider');
        var settingCtxInput = document.getElementById('setting-ctx-input');
        var settingRatioSlider = document.getElementById('setting-ratio-slider');
        var settingRatioInput = document.getElementById('setting-ratio-input');
        var settingOutSlider = document.getElementById('setting-out-slider');
        var settingOutInput = document.getElementById('setting-out-input');

        // 加载服务商列表（litellm provider 路由前缀）
        async function loadProviders(selectedId) {
            try {
                var resp = await fetch('/api/llm-providers');
                var data = await resp.json();
                providerList = data.providers || [];
                providersLoaded = true;
            } catch (e) {
                console.log('加载服务商列表失败:', e);
                providerList = [];
                providersLoaded = false;
            }
            renderProviderOptions(selectedId);
        }

        function renderProviderOptions(selectedId) {
            if (!settingProvider) return;
            // 保留当前选中值，若列表里没有则追加一个"自定义"项
            var prev = selectedId || settingProvider.value;
            var opts = '';
            for (var i = 0; i < providerList.length; i++) {
                var p = providerList[i];
                opts += '<option value="' + p.id + '"' + (p.id === prev ? ' selected' : '') + '>'
                      + p.name + ' (' + p.id + ')</option>';
            }
            // 若当前 provider 不在列表中，追加一个自定义项保证可见
            if (prev && !providerList.some(function(p){ return p.id === prev; })) {
                opts = '<option value="' + prev + '" selected>' + prev + ' (自定义)</option>' + opts;
            }
            if (!prev) {
                opts = '<option value="" selected>— 请选择 —</option>' + opts;
            }
            settingProvider.innerHTML = opts;
            updateProviderHint();
        }

        function updateProviderHint() {
            if (!modelPickHint || !settingProvider) return;
            var id = settingProvider.value;
            var found = providerList.filter(function(p){ return p.id === id; })[0];
            modelPickHint.textContent = found ? found.desc : (id ? '自定义服务商前缀：' + id : '');
        }

        /** 根据选中的 provider 自动填入默认 api_base */
        function applyDefaultApiBase(force) {
            if (!settingApiBase || !settingProvider) return;
            var id = settingProvider.value;
            if (!id) return;
            var found = providerList.filter(function(p){ return p.id === id; })[0];
            if (found && found.default_api_base) {
                var current = settingApiBase.value.trim();
                // force=true 时总是覆盖（用于 provider 切换）
                // 非 force 时只覆盖空字段或之前由本函数填入的值
                if (force || !current || current === settingApiBase._autoFillValue) {
                    settingApiBase.value = found.default_api_base;
                    settingApiBase._autoFillValue = found.default_api_base;
                }
            }
        }

        /** 点击"获取列表"按钮时从服务商拉取模型列表并填入 datalist */
        var modelNameList = document.getElementById('model-name-list');
        var btnFetchModels = document.getElementById('btn-fetch-models');
        function isCloudEndpoint(apiBase) {
            return apiBase && !/^https?:\/\/localhost[:\/]/i.test(apiBase) && !/^https?:\/\/127\.0\.0\.1[:\/]/i.test(apiBase);
        }
        async function fetchModelList() {
            if (!settingProvider || !settingApiBase || !modelNameList) return;
            var providerId = settingProvider.value;
            var apiBase = settingApiBase.value.trim();
            if (!providerId || !apiBase) return;

            // 清空模型名和 datalist，避免旧值干扰下拉筛选
            if (settingModelName) settingModelName.value = '';
            modelNameList.innerHTML = '';

            btnFetchModels.disabled = true;
            btnFetchModels.textContent = '⏳ 加载中...';

            try {
                var url = '/api/llm-providers/' + encodeURIComponent(providerId) + '/models'
                    + '?api_base=' + encodeURIComponent(apiBase);
                // 用户在输入框填了 key 就直接传给后端获取模型列表
                // 这样做不用先保存设置就能拉列表，解决"要先选模型才能保存key，要key才能获取模型列表"的死锁
                if (settingApiKey && settingApiKey.value.trim()) {
                    url += '&api_key=' + encodeURIComponent(settingApiKey.value.trim());
                }
                var resp = await fetch(url);
                var data = await resp.json();
                var models = data.models || [];
                var errMsg = data.error || '';

                // 填入 datalist
                modelNameList.innerHTML = '';
                for (var i = 0; i < models.length; i++) {
                    var opt = document.createElement('option');
                    opt.value = models[i];
                    modelNameList.appendChild(opt);
                }

                if (models.length > 0) {
                    btnFetchModels.textContent = '✅ ' + models.length + ' 个模型';
                } else if (errMsg) {
                    btnFetchModels.textContent = '❌ ' + errMsg;
                    // 显示错误详情到 hint 区域
                    if (modelPickHint) modelPickHint.textContent = '⚠️ ' + errMsg;
                } else {
                    btnFetchModels.textContent = '😕 无可用模型';
                }
                setTimeout(function() {
                    btnFetchModels.textContent = '📋 获取列表';
                    btnFetchModels.disabled = false;
                }, 3000);
            } catch (e) {
                console.log('获取模型列表失败:', e);
                btnFetchModels.textContent = '❌ 加载失败';
                setTimeout(function() {
                    btnFetchModels.textContent = '📋 获取列表';
                    btnFetchModels.disabled = false;
                }, 2000);
            }
        }

        // 绑定"获取列表"按钮
        if (btnFetchModels) {
            btnFetchModels.addEventListener('click', fetchModelList);
        }

        // 加载设置
        async function loadSettings() {
            try {
                var resp = await fetch('/api/settings');
                var settings = await resp.json();
                // 先确保 provider 下拉已填充，再回填选中值
                if (!providersLoaded) {
                    await loadProviders(settings.provider);
                } else if (settings.provider) {
                    // 已加载过列表，直接回填（不存在则追加自定义项）
                    renderProviderOptions(settings.provider);
                } else {
                    renderProviderOptions('');
                }
                if (settingModelName) {
                    settingModelName.value = settings.model_name || '';
                }
                if (settingTempSlider && settings.temperature != null) {
                    var val = Math.round(settings.temperature * 100);
                    settingTempSlider.value = val;
                    if (settingTempInput) settingTempInput.value = settings.temperature.toFixed(2);
                }
                if (settingApiBase && settings.api_base) settingApiBase.value = settings.api_base;
                // API Base 回填后，若仍为空则尝试从 provider 默认值自动填充
                if (settingApiBase && !settingApiBase.value.trim()) {
                    applyDefaultApiBase();
                }
                // 最大输出 Token（后端存 raw，前端显示 K）
                if (settings.max_tokens != null) {
                    var outK = Math.max(1, Math.min(32, Math.round(settings.max_tokens / 1024)));
                    if (settingOutSlider) settingOutSlider.value = outK;
                    if (settingOutInput) settingOutInput.value = outK;
                }
                // API Key：仅显示是否已配置，绝不回填明文
                apiKeyConfigured = !!settings.has_api_key;
                if (settingApiKey) {
                    settingApiKey.value = '';
                    settingApiKey.placeholder = apiKeyConfigured
                        ? '已设置，输入新值可替换'
                        : '输入新的 API Key';
                }
                if (apiKeyStatus) {
                    apiKeyStatus.textContent = apiKeyConfigured ? '✓ 已配置' : '未配置';
                    apiKeyStatus.className = 'api-key-status' + (apiKeyConfigured ? ' ok' : '');
                }
                // Preserve Thinking
                if (settingPreserveThinking) {
                    settingPreserveThinking.checked = !!settings.preserve_thinking;
                }
                if (ptStatus) {
                    ptStatus.textContent = settings.preserve_thinking ? '开启' : '关闭';
                }
                // 权限模式
                var permModeSelect = document.getElementById('perm-mode-select');
                if (permModeSelect && settings.permission_mode) {
                    permModeSelect.value = settings.permission_mode;
                }
                // 上下文窗口（K）
                if (settingCtxSlider && settings.context_window_k != null) {
                    var ctxK = Math.max(32, Math.min(1024, settings.context_window_k));
                    settingCtxSlider.value = ctxK;
                    if (settingCtxInput) settingCtxInput.value = ctxK;
                }
                // 压缩阈值
                if (settingRatioInput && settings.token_limit_ratio != null) {
                    var ratioVal = Math.max(0.1, Math.min(1.0, settings.token_limit_ratio));
                    var ratioSliderVal = Math.round(ratioVal * 100);
                    if (settingRatioSlider) settingRatioSlider.value = ratioSliderVal;
                    settingRatioInput.value = ratioVal.toFixed(2);
                }
            } catch (e) {
                console.log('加载设置失败:', e);
            }
        }

        // 温度滑块 → 输入框联动
        if (settingTempSlider && settingTempInput) {
            settingTempSlider.addEventListener('input', function() {
                var val = parseInt(this.value, 10);
                settingTempInput.value = (val / 100).toFixed(2);
            });
        }

        // 输入框 → 滑块联动
        if (settingTempInput && settingTempSlider) {
            settingTempInput.addEventListener('input', function() {
                var val = parseFloat(this.value);
                if (!isNaN(val) && val >= 0 && val <= 2) {
                    settingTempSlider.value = Math.round(val * 100);
                }
            });
        }

        // 保存设置
        async function saveSettings() {
            // === 必填项校验 ===
            var provider = settingProvider ? settingProvider.value.trim() : '';
            var modelName = settingModelName ? settingModelName.value.trim() : '';
            var apiBase = settingApiBase ? settingApiBase.value.trim() : '';
            var errors = [];

            if (!provider) errors.push('请选择模型供应商');
            if (!modelName) errors.push('请输入或选择模型名称');
            if (!apiBase) errors.push('API Base 不能为空');
            // 云服务商需要 API Key
            if (provider && apiBase && isCloudEndpoint(apiBase) && !apiKeyConfigured) {
                var keyInput = settingApiKey ? settingApiKey.value.trim() : '';
                if (!keyInput) errors.push('云服务商需要配置 API Key');
            }

            if (errors.length > 0) {
                if (modelPickHint) modelPickHint.textContent = '⚠️ ' + errors.join('；');
                // 高亮提示 3 秒后消失
                setTimeout(function() {
                    if (modelPickHint && modelPickHint.textContent.startsWith('⚠️')) {
                        modelPickHint.textContent = '';
                    }
                }, 4000);
                return;
            }

            var payload = {};
            // 模型：provider + model_name 由后端拼接为 "provider/model_name"
            if (settingProvider) payload.provider = settingProvider.value;
            if (settingModelName) payload.model_name = settingModelName.value;
            if (settingTempInput) {
                var v = parseFloat(settingTempInput.value);
                if (!isNaN(v) && v >= 0 && v <= 2) payload.temperature = v;
            } else if (settingTempSlider) {
                payload.temperature = parseInt(settingTempSlider.value, 10) / 100;
            }
            if (settingApiBase) payload.api_base = settingApiBase.value;
            // 最大输出 Token：K → raw tokens
            if (settingOutInput) {
                payload.max_tokens = (parseInt(settingOutInput.value, 10) || 8) * 1024;
            }
            // API Key：仅当用户输入了非空新值才提交，避免误清空已保存的密钥
            if (settingApiKey && settingApiKey.value.trim()) {
                payload.api_key = settingApiKey.value.trim();
            }
            // Preserve Thinking
            if (settingPreserveThinking) {
                payload.preserve_thinking = settingPreserveThinking.checked;
            }
            // 权限模式
            var permModeSelect = document.getElementById('perm-mode-select');
            if (permModeSelect) {
                payload.permission_mode = permModeSelect.value;
            }
            // 上下文窗口（K → 后端自己换算）
            if (settingCtxInput) {
                payload.context_window_k = parseInt(settingCtxInput.value, 10) || 128;
            }
            // 压缩阈值
            if (settingRatioInput) {
                payload.token_limit_ratio = parseFloat(settingRatioInput.value) || 0.8;
            }
            try {
                await fetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } catch (e) {
                console.error('保存设置失败:', e);
            }
        }

        // 设置页显示时加载
        var settingsScreen = document.getElementById('screen-settings');
        if (settingsScreen) {
            var observer = new MutationObserver(function() {
                if (settingsScreen.classList.contains('active')) {
                    loadSettings();
                }
            });
            observer.observe(settingsScreen, { attributes: true, attributeFilter: ['class'] });
        }

        // 输入变化自动保存
        if (settingProvider) settingProvider.addEventListener('change', function() {
            updateProviderHint();
            applyDefaultApiBase(true);  // force=true：切换 provider 时始终覆盖 api_base
            saveSettings();
        });
        if (settingModelName) settingModelName.addEventListener('change', saveSettings);
        if (settingTempSlider) settingTempSlider.addEventListener('change', saveSettings);
        if (settingTempInput) settingTempInput.addEventListener('change', saveSettings);
        if (settingApiBase) settingApiBase.addEventListener('change', function() {
            this._autoFillValue = null;
            saveSettings();
        });
        if (settingOutSlider && settingOutInput) {
            settingOutSlider.addEventListener('input', function() {
                settingOutInput.value = this.value;
            });
            settingOutSlider.addEventListener('change', saveSettings);
            settingOutInput.addEventListener('change', function() {
                var v = parseInt(this.value, 10);
                if (isNaN(v) || v < 1) v = 1;
                if (v > 32) v = 32;
                this.value = v;
                settingOutSlider.value = v;
                saveSettings();
            });
        }
        if (settingMaxTokens) settingMaxTokens.addEventListener('change', saveSettings);
        if (settingPreserveThinking) settingPreserveThinking.addEventListener('change', function() {
            ptStatus.textContent = this.checked ? '开启' : '关闭';
            saveSettings();
        });

        // ── TTS 设置 ──
        var ttsToggle = document.getElementById('tts-toggle');
        var ttsApiKey = document.getElementById('tts-api-key');
        var ttsMode = document.getElementById('tts-mode');
        var ttsVoice = document.getElementById('tts-voice');
        var ttsDesignPrompt = document.getElementById('tts-design-prompt');
        var ttsDesignRow = document.getElementById('tts-design-row');
        var ttsCloneRow = document.getElementById('tts-clone-row');
        var ttsCloneStyleRow = document.getElementById('tts-clone-style-row');
        var ttsVoiceRow = document.getElementById('tts-voice-row');
        var ttsCharSelect = document.getElementById('tts-char-select');
        // 当前配置中的角色 ID（可能不同于当前聊天角色）
        var ttsTone = document.getElementById('tts-tone');
        var btnTestTTS = document.getElementById('btn-test-tts');
        var ttsTestStatus = document.getElementById('tts-test-status');

        // TTS 开关（localStorage 持久化）
        if (ttsToggle) {
            var saved = localStorage.getItem('tts_enabled') === 'true';
            AIGEME.shared.settings.ttsEnabled = saved;
            ttsToggle.checked = saved;
            if (typeof TTSPlayer !== 'undefined') {
                TTSPlayer.setEnabled(saved);
            }
            ttsToggle.addEventListener('change', function() {
                AIGEME.shared.settings.ttsEnabled = this.checked;
                localStorage.setItem('tts_enabled', this.checked);
                if (typeof TTSPlayer !== 'undefined') {
                    TTSPlayer.setEnabled(this.checked);
                }
            });
        }

        // TTS 自动保存到 localStorage 和本地存储
        function saveTTSLocally() {
            var charId = getTTSSelChar();
            var mode = ttsMode ? ttsMode.value : 'preset';
            var data = {
                mode: mode,
                voice: ttsVoice ? ttsVoice.value : '冰糖',
                tone: ttsTone ? ttsTone.value : '自然温和',
            };
            // 按模式只存相关字段
            if (mode === 'voice_design') {
                data.voice_design_prompt = ttsDesignPrompt ? ttsDesignPrompt.value : '';
            } else if (mode === 'voice_clone') {
                var styleEl = document.getElementById('tts-clone-style');
                if (styleEl) data.voice_clone_style_desc = styleEl.value;
            }
            localStorage.setItem('tts_config_' + charId, JSON.stringify(data));
        }

        // TTS 变更时自动保存本地 + 更新 UI
        function bindTTSSave(el) {
            if (el) el.addEventListener('change', saveTTSLocally);
        }
        bindTTSSave(ttsMode);
        bindTTSSave(ttsVoice);
        bindTTSSave(ttsDesignPrompt);
        bindTTSSave(document.getElementById('tts-clone-style'));
        bindTTSSave(ttsTone);

        // TTS 模式切换 → 显示/隐藏对应行 + 样本状态
        function updateTTSModeUI() {
            var mode = ttsMode ? ttsMode.value : 'preset';
            if (ttsVoiceRow) ttsVoiceRow.style.display = (mode === 'preset') ? '' : 'none';
            if (ttsDesignRow) ttsDesignRow.style.display = (mode === 'voice_design') ? '' : 'none';
            if (ttsCloneRow) ttsCloneRow.style.display = (mode === 'voice_clone') ? '' : 'none';
            if (ttsCloneStyleRow) ttsCloneStyleRow.style.display = (mode === 'voice_clone') ? '' : 'none';

            // 语音克隆模式下显示已保存样本信息
            if (mode === 'voice_clone') {
                var charId = getTTSSelChar();
                var sampleName = localStorage.getItem('tts_clone_name_' + charId);
                var statusEl = document.getElementById('tts-clone-status');
                if (!statusEl) {
                    var row = document.getElementById('tts-clone-row');
                    if (row) {
                        statusEl = document.createElement('span');
                        statusEl.id = 'tts-clone-status';
                        statusEl.style.cssText = 'font-size:0.8rem;color:var(--text-hint);margin-left:8px;';
                        row.querySelector('label')?.after(statusEl);
                    }
                }
                if (statusEl) {
                    statusEl.textContent = sampleName ? '✅ ' + sampleName : '';
                }
            }
        }
        if (ttsMode) {
            ttsMode.addEventListener('change', updateTTSModeUI);
        }

        // 获取当前 TTS 配置对应的角色 ID
        function getTTSSelChar() {
            return ttsCharSelect ? ttsCharSelect.value : (AIGEME.chat.currentChar && AIGEME.chat.currentChar.id) || 'ario';
        }

        // 填充角色下拉并选中当前角色
        function populateTTSCharSelect() {
            if (!ttsCharSelect) return;
            var chars = AIGEME.chat.characters || [];

            // 如果列表不完整（空或只有1个），调用 API 获取完整列表
            if (chars.length <= 1) {
                fetch('/api/characters').then(function(r) { return r.json(); }).then(function(allChars) {
                    AIGEME.chat.characters = allChars;
                    renderCharOptions(allChars);
                }).catch(function() {
                    renderCharOptions(chars);
                });
            } else {
                renderCharOptions(chars);
            }
        }

        function renderCharOptions(chars) {
            if (!ttsCharSelect) return;
            var currentId = (AIGEME.chat.currentChar && AIGEME.chat.currentChar.id) || 'ario';
            ttsCharSelect.innerHTML = '';
            if (chars.length === 0) {
                var opt = document.createElement('option');
                opt.value = currentId;
                opt.textContent = currentId;
                ttsCharSelect.appendChild(opt);
            } else {
                for (var i = 0; i < chars.length; i++) {
                    var c = chars[i];
                    var opt = document.createElement('option');
                    opt.value = c.id;
                    opt.textContent = c.name || c.id;
                    ttsCharSelect.appendChild(opt);
                }
            }
            ttsCharSelect.value = currentId;
        }

        // 从后端加载指定角色的 TTS 配置，失败时回退 localStorage
        function loadTTSConfig() {
            var charId = getTTSSelChar();
            var charName = '';
            var chars = AIGEME.chat.characters || [];
            for (var i = 0; i < chars.length; i++) {
                if (chars[i].id === charId) { charName = chars[i].name || charId; break; }
            }
            if (!charName) charName = charId;

            // 标题显示角色名
            var titleEl = document.querySelector('#tts-settings-block h3');
            if (titleEl) titleEl.textContent = '🎤 TTS 语音设置 — ' + charName;

            // 填充 UI 的公共函数
            function fillTTSData(data) {
                if (ttsMode) ttsMode.value = data.mode || 'preset';
                if (ttsVoice) ttsVoice.value = data.voice || '冰糖';
                if (ttsDesignPrompt) ttsDesignPrompt.value = (data.mode === 'voice_design' && data.voice_design_prompt) ? data.voice_design_prompt : '';
                if (ttsTone) ttsTone.value = data.tone || '自然温和';
                var cloneStyleEl = document.getElementById('tts-clone-style');
                if (cloneStyleEl) cloneStyleEl.value = data.voice_clone_style_desc || '';
                updateTTSModeUI();
            }

            // 从后端加载（跨设备时总能拿到最新配置）
            fetch('/api/tts/config?character=' + charId).then(function(r) { return r.json(); }).then(function(data) {
                if (ttsApiKey && data.has_api_key) {
                    ttsApiKey.value = '••••••••';
                    localStorage.setItem('tts_api_key_' + charId, '1');
                } else if (ttsApiKey) {
                    ttsApiKey.value = '';
                    ttsApiKey.placeholder = '当前设备未配置 API Key，请在此输入并保存';
                }
                fillTTSData(data);
                // 同步 localStorage 作为缓存（同一设备离线降级用）
                var cacheData = {
                    mode: data.mode || 'preset',
                    voice: data.voice || '冰糖',
                    tone: data.tone || '自然温和',
                };
                if (data.mode === 'voice_design') {
                    cacheData.voice_design_prompt = data.voice_design_prompt || '';
                } else if (data.mode === 'voice_clone') {
                    cacheData.voice_clone_style_desc = data.voice_clone_style_desc || '';
                }
                localStorage.setItem('tts_config_' + charId, JSON.stringify(cacheData));
            }).catch(function(e) {
                console.warn('[TTS] 后端加载失败，回退 localStorage:', e);
                // 后端不可用时回退到 localStorage 缓存
                var local = localStorage.getItem('tts_config_' + charId);
                if (local) {
                    try {
                        var data = JSON.parse(local);
                        fillTTSData(data);
                        var savedKey = localStorage.getItem('tts_api_key_' + charId);
                        if (ttsApiKey) ttsApiKey.value = savedKey ? '••••••••' : '';
                    } catch (e2) {}
                }
            });
        }

        // TTS 角色切换 → 重新加载配置
        if (ttsCharSelect) {
            ttsCharSelect.addEventListener('change', function() {
                // 切换角色时把 toggle 也相应切换
                var charId = getTTSSelChar();
                var savedToggle = localStorage.getItem('tts_enabled_' + charId);
                if (savedToggle !== null) {
                    AIGEME.shared.settings.ttsEnabled = savedToggle === 'true';
                    if (ttsToggle) ttsToggle.checked = savedToggle === 'true';
                    if (typeof TTSPlayer !== 'undefined') TTSPlayer.setEnabled(savedToggle === 'true');
                }
                loadTTSConfig();
            });
        }

        // 测试语音按钮 — 使用下拉选择的角色
        if (btnTestTTS) {
            btnTestTTS.addEventListener('click', async function() {
                if (ttsTestStatus) ttsTestStatus.textContent = '合成中...';
                var charId = getTTSSelChar();
                var apiKey = ttsApiKey ? ttsApiKey.value.trim() : '';
                var mode = ttsMode ? ttsMode.value : 'preset';

                var payload = {
                    text: '你好，欢迎体验小米智能语音合成。今天天气晴朗，是一个适合外出散步的好日子。',
                    character: charId,
                    config: {
                        mode: mode,
                        voice: ttsVoice ? ttsVoice.value : '冰糖',
                        voice_design_prompt: ttsDesignPrompt ? ttsDesignPrompt.value : '',
                        tone: ttsTone ? ttsTone.value : '自然温和',
                    },
                };

                // 语音克隆：优先用已保存的 base64，其次从文件读取
                if (mode === 'voice_clone') {
                    var savedB64 = localStorage.getItem('tts_clone_b64_' + charId);
                    var fileInput = document.getElementById('tts-clone-sample');
                    if (savedB64) {
                        payload.config.voice_clone_sample = savedB64;
                    } else if (fileInput && fileInput.files && fileInput.files.length > 0) {
                        var file = fileInput.files[0];
                        var reader = new FileReader();
                        var b64Promise = new Promise(function(resolve, reject) {
                            reader.onload = function(e) {
                                var b64 = e.target.result.split(',')[1];
                                var mime = file.type || 'audio/wav';
                                var dataUrl = 'data:' + mime + ';base64,' + b64;
                                payload.config.voice_clone_sample = dataUrl;
                                // 保存到 localStorage 供后续使用
                                localStorage.setItem('tts_clone_b64_' + charId, dataUrl);
                                localStorage.setItem('tts_clone_name_' + charId, file.name);
                                resolve();
                            };
                            reader.onerror = reject;
                            reader.readAsDataURL(file);
                        });
                        await b64Promise;
                    } else {
                        if (ttsTestStatus) ttsTestStatus.textContent = '❌ 请选择音频样本';
                        return;
                    }
                }
                if (apiKey && apiKey.indexOf('••••') === -1) {
                    payload.api_key = apiKey;
                }
                try {
                    var resp = await fetch('/api/tts/test', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload),
                    });
                    var data = await resp.json();
                    if (data.status === 'ok' && typeof TTSPlayer !== 'undefined') {
                        TTSPlayer.playTest(data.audio_data, 0);
                        if (ttsTestStatus) ttsTestStatus.textContent = '✅ 播放中';
                    } else {
                        if (ttsTestStatus) ttsTestStatus.textContent = '❌ ' + (data.message || '合成失败');
                    }
                } catch (e) {
                    if (ttsTestStatus) ttsTestStatus.textContent = '❌ 请求失败';
                }
            });
        }

        // 在设置面板打开时填充角色列表 + 加载 TTS 配置
        var screenSettings = document.getElementById('screen-settings');
        if (screenSettings) {
            var observer = new MutationObserver(function() {
                if (screenSettings.classList.contains('active') && typeof TTSPlayer !== 'undefined') {
                    populateTTSCharSelect();
                    loadTTSConfig();
                }
            });
            observer.observe(screenSettings, {attributes: true, attributeFilter: ['class']});
        }

        // 上下文窗口 滑块 ↔ 输入框联动 + 自动保存
        if (settingCtxSlider && settingCtxInput) {
            settingCtxSlider.addEventListener('input', function() {
                settingCtxInput.value = this.value;
            });
            settingCtxSlider.addEventListener('change', saveSettings);
            settingCtxInput.addEventListener('change', function() {
                var v = parseInt(this.value, 10);
                if (isNaN(v) || v < 32) v = 32;
                if (v > 1024) v = 1024;
                this.value = v;
                settingCtxSlider.value = v;
                saveSettings();
            });
        }
        // 压缩阈值 滑块 ↔ 输入框联动 + 自动保存
        if (settingRatioSlider && settingRatioInput) {
            settingRatioSlider.addEventListener('input', function() {
                var v = (parseInt(this.value, 10) / 100).toFixed(2);
                settingRatioInput.value = v;
            });
            settingRatioSlider.addEventListener('change', saveSettings);
            settingRatioInput.addEventListener('change', function() {
                var v = parseFloat(this.value);
                if (isNaN(v) || v < 0.1) v = 0.1;
                if (v > 1.0) v = 1.0;
                this.value = v.toFixed(2);
                settingRatioSlider.value = Math.round(v * 100);
                saveSettings();
            });
        }

        // 权限模式自动保存
        var permModeSelect = document.getElementById('perm-mode-select');
        if (permModeSelect) {
            permModeSelect.addEventListener('change', saveSettings);
        }

        // 保存按钮
        var btnSave = document.getElementById('btn-save-settings');
        if (btnSave) {
            btnSave.addEventListener('click', async function() {
                await saveSettings();
                // 保存后清空 API Key 输入框（明文不应留在 DOM 中），并刷新状态
                if (settingApiKey && settingApiKey.value) {
                    settingApiKey.value = '';
                    apiKeyConfigured = true;
                    settingApiKey.placeholder = '已设置，输入新值可替换';
                    if (apiKeyStatus) {
                        apiKeyStatus.textContent = '✓ 已配置';
                        apiKeyStatus.className = 'api-key-status ok';
                    }
                }
                // 保存 TTS 配置
                try {
                    var charId = getTTSSelChar();
                    var mode = ttsMode ? ttsMode.value : 'preset';
                    var ttsPayload = {
                        character: charId,
                        mode: mode,
                        voice: ttsVoice ? ttsVoice.value : '冰糖',
                        tone: ttsTone ? ttsTone.value : '自然温和',
                    };
                    // 按模式只发相关字段
                    if (mode === 'voice_design') {
                        ttsPayload.voice_design_prompt = ttsDesignPrompt ? ttsDesignPrompt.value : '';
                    } else if (mode === 'voice_clone') {
                        var styleEl = document.getElementById('tts-clone-style');
                        if (styleEl) ttsPayload.voice_clone_style_desc = styleEl.value;
                        // 带上语音克隆样本 base64，后端会存为文件
                        var savedB64 = localStorage.getItem('tts_clone_b64_' + charId);
                        if (savedB64) {
                            ttsPayload.voice_clone_sample = savedB64;
                        }
                    }
                    if (ttsApiKey && ttsApiKey.value && ttsApiKey.value.indexOf('••••') === -1) {
                        ttsPayload.api_key = ttsApiKey.value;
                    }
                    await fetch('/api/tts/config', {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(ttsPayload),
                    });
                } catch (e) {
                    console.warn('[TTS] 保存配置失败:', e);
                }
                var originalText = btnSave.textContent;
                btnSave.textContent = '✅ 已保存';
                setTimeout(function() {
                    btnSave.textContent = originalText;
                }, 2000);
            });
        }

        // ==========================================
        // 10b. API Key 安全防护 — 禁止拖拽/选中明文，输入即掩码
        // ==========================================
        if (settingApiKey) {
            // 禁止拖拽选中文本到外部
            settingApiKey.addEventListener('dragstart', function(e) { e.preventDefault(); });
            // 禁止选中（password 类型本身不可选中，双保险）
            settingApiKey.addEventListener('select', function(e) { e.preventDefault(); });
            // 输入时实时掩码（type=password 已保证，此处仅清掉误粘贴的前后空白）
            settingApiKey.addEventListener('input', function() {
                // 首次输入时清除"已配置"提示，让用户知道正在输入新值
                if (apiKeyStatus && apiKeyStatus.textContent.indexOf('输入中') === -1) {
                    apiKeyStatus.textContent = '输入中…';
                    apiKeyStatus.className = 'api-key-status';
                }
            });
        }
        // 清空输入按钮
        if (apiKeyClear && settingApiKey) {
            apiKeyClear.addEventListener('click', function() {
                settingApiKey.value = '';
                settingApiKey.focus();
                if (apiKeyStatus) {
                    apiKeyStatus.textContent = apiKeyConfigured ? '✓ 已配置' : '未配置';
                    apiKeyStatus.className = 'api-key-status' + (apiKeyConfigured ? ' ok' : '');
                }
            });
        }

        // ==========================================
        // 11. 预加载默认立绘 + 初始屏
        // ==========================================
        var defaultChar = AIGEME.chat.currentChar;
        if (!defaultChar) {
            AIGEME.shared.screen = 'title-screen';
        }

        // 应用已保存的主题
        AIGEME.applyTheme();

        // 设置默认立绘（如果有角色）
        if (AIGEME.chat.currentChar) {
            AIGEME_UI.setTachie('default');
        }

        // ==========================================
        // 12. 文字速度滑块联动打字机速度
        // ==========================================
        var speedSlider = document.getElementById('setting-speed');
        if (speedSlider) {
            // 从 localStorage 恢复
            var savedSpeed = localStorage.getItem('aigeme-speed');
            if (savedSpeed !== null) {
                speedSlider.value = savedSpeed;
            }
            // 映射: slider值(5-80) → 速度ms, 默认30→40ms, 公式 1200/value
            function updateSpeed() {
                var val = parseInt(speedSlider.value, 10) || 30;
                AIGEME.chat.typewriter.speed = Math.max(10, Math.round(1200 / val));
                localStorage.setItem('aigeme-speed', val);
            }
            speedSlider.addEventListener('input', updateSpeed);
            // 初始应用
            updateSpeed();
        }

        // ── TTS 重播按钮 ──
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('.tts-replay-btn');
            if (!btn) return;
            // 优先使用 data-tts-turn-id（后端 TTS 缓存的 turn_id），回退到 data-turn-id（UI 轮次号）
            var turnId = btn.getAttribute('data-tts-turn-id') || btn.getAttribute('data-turn-id');
            if (!turnId) return;
            var charId = (AIGEME.chat.currentChar && AIGEME.chat.currentChar.id) || 'ario';
            fetch('/api/tts/cache/' + charId + '/' + turnId).then(function(r) { return r.json(); }).then(function(data) {
                if (data.status === 'ok' && typeof TTSPlayer !== 'undefined') {
                    TTSPlayer.playTest(data.audio_data, 0);
                }
            }).catch(function() {});
        });
    });

    // 窗口resize时重新计算立绘比例
    window.addEventListener('resize', function() {
        AIGEME.adjustSpriteRatio();
    });

    // 页面关闭前断开 WebSocket
    window.addEventListener('beforeunload', function() {
        WSClient.disconnect();
    });
})();

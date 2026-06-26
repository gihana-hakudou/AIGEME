/**
 * WebSocket 客户端模块
 * 连接/重连、发送/接收 Block 事件、心跳
 */
const WSClient = {
    ws: null,
    reconnectTimer: null,
    pingInterval: null,
    isConnected: false,
    retryCount: 0,
    maxRetries: 10,
    url: null,

    /** 连接到 WebSocket 服务器 */
    connect: function(characterId) {
        this.url = `${AIGEME.shared.settings.wsUrl}/ws/${characterId}`;
        this._createConnection();
    },

    _createConnection: function() {
        if (this.ws) {
            this.ws.close();
        }

        try {
            this.ws = new WebSocket(this.url);
        } catch (e) {
            console.error('WebSocket 连接失败:', e);
            this._scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket 已连接');
            this.retryCount = 0;
            this.isConnected = true;
            AIGEME.shared.connected = true;
            AIGEME.shared.ws = this.ws;
            this._startPing();

            // 新连接建立时，同步 _speechTurnId 与当前 turnId，
            // 确保 identity speech block 能正常通过 turnId 检查
            AIGEME.chat._speechTurnId = AIGEME.chat.turnId;

            // 发送角色选择消息
            if (AIGEME.chat.currentChar) {
                this.ws.send(JSON.stringify({
                    type: 'user_message',
                    content: '',
                    character_id: AIGEME.chat.currentChar.id,
                    mode: 'single',
                    images: [],
                }));
            }
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this._handleMessage(data);
            } catch (e) {
                console.error('解析消息失败:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('WebSocket 已断开');
            this.isConnected = false;
            AIGEME.shared.connected = false;
            AIGEME.shared.ws = null;
            this._stopPing();
            this._scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket 错误:', err);
        };
    },

    /** 处理收到的消息 */
    _handleMessage: function(data) {
        if (data.type === 'pong') return;
        if (data.type === 'block') {
            if (data.block_type === 'speech' && window.soundManager) {
                window.soundManager.play('message_receive');
            }
            BlockRenderer.handle(data);
        }
    },

    /** 发送消息 */
    send: function(data) {
        if (this.ws && this.isConnected) {
            this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
        }
    },

    /** 心跳 ping */
    _startPing: function() {
        this._stopPing();
        this.pingInterval = setInterval(() => {
            if (this.ws && this.isConnected) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    },

    _stopPing: function() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    },

    /** 重连（指数退避） */
    _scheduleReconnect: function() {
        if (this.reconnectTimer) return;
        if (this.retryCount >= this.maxRetries) {
            console.error('WebSocket 已达最大重试次数 ' + this.maxRetries + '，停止重连');
            return;
        }
        var delay = Math.min(3000 * Math.pow(2, this.retryCount), 60000);
        this.retryCount++;
        console.log('尝试重新连接... (第' + this.retryCount + '次, ' + delay + 'ms后)');
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            if (this.url && !this.isConnected) {
                this._createConnection();
            }
        }, delay);
    },

    /** 断开连接 */
    disconnect: function() {
        this._stopPing();
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        AIGEME.shared.connected = false;
        AIGEME.shared.ws = null;
    },
};

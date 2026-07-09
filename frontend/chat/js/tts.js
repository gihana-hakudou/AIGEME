/**
 * TTSPlayer — 前端 TTS 音频播放模块
 * 
 * 职责：
 * - 管理 AudioContext 和音频播放
 * - 严格按 index 顺序播放 audio block
 * - 中断机制：新消息时停止所有播放
 * - 播放/停止控制
 */
const TTSPlayer = {
    // ── 状态 ──
    enabled: false,
    isPlaying: false,
    audioQueue: [],         // [{index, audioBuffer}]
    currentSource: null,    // AudioBufferSourceNode
    audioContext: null,     // AudioContext (懒初始化)
    nextPlayIndex: 0,       // 下一个应播放的序号
    turnId: 0,              // 当前轮次 ID（用于中断判定）
    _playTimer: null,       // 播放状态轮询 timer

    // ── 初始化 ──
    init: function() {
        var saved = localStorage.getItem('tts_enabled') === 'true';
        this.enabled = saved;
        if (typeof AIGEME !== 'undefined') {
            AIGEME.shared.settings.ttsEnabled = saved;
        }
        this._generation = 0;
    },

    // ── 开关控制 ──
    setEnabled: function(enabled) {
        this.enabled = enabled;
        AIGEME.shared.settings.ttsEnabled = enabled;
        localStorage.setItem('tts_enabled', enabled);
        if (!enabled) {
            this.stop();
        }
    },

    // ── AudioContext 懒初始化 ──
    _ensureContext: function() {
        if (!this.audioContext) {
            try {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            } catch (e) {
                console.warn('[TTS] AudioContext 不可用:', e);
                return null;
            }
        }
        if (this.audioContext.state === 'suspended') {
            this.audioContext.resume();
        }
        return this.audioContext;
    },

    // ── 接收 audio block ──
    play: function(audioDataBase64, index) {
        if (!this.enabled) return;
        this._doPlay(audioDataBase64, index);
    },

    /** 强制播放（不检查 enabled，给测试按钮用） */
    playTest: function(audioDataBase64, index) {
        // 重置播放状态，避免上一次的 nextPlayIndex 导致音频被丢弃
        this._stopCurrent();
        this.audioQueue = [];
        this.nextPlayIndex = 0;
        this._doPlay(audioDataBase64, index || 0);
    },

    _doPlay: function(audioDataBase64, index) {

        var ctx = this._ensureContext();
        if (!ctx) return;

        // 解码 base64 → ArrayBuffer
        var binaryStr = atob(audioDataBase64);
        var len = binaryStr.length;
        var bytes = new Uint8Array(len);
        for (var i = 0; i < len; i++) {
            bytes[i] = binaryStr.charCodeAt(i);
        }

        var self = this;
        var gen = this._generation;
        ctx.decodeAudioData(bytes.buffer, function(buffer) {
            // 如果 generation 变了（被 interrupt 过），丢弃
            if (gen !== self._generation) return;
            self._onAudioDecoded(buffer, index);
        }, function(err) {
            console.warn('[TTS] 音频解码失败:', err);
        });
    },

    _onAudioDecoded: function(buffer, index) {
        // 如果 index 小于当前播放序号，丢弃
        if (index < this.nextPlayIndex) {
            console.log('[TTS] 丢弃过期音频 index=' + index + ', nextPlayIndex=' + this.nextPlayIndex);
            return;
        }

        // 还没轮到，或正在播放中 → 放入队列
        if (index > this.nextPlayIndex || this.isPlaying) {
            this.audioQueue.push({index: index, buffer: buffer});
            this.audioQueue.sort(function(a, b) { return a.index - b.index; });
            return;
        }

        // 立即轮到且没有正在播放 → 播放
        this._playBuffer(buffer, index);
    },

    _playBuffer: function(buffer, index) {
        var ctx = this._ensureContext();
        if (!ctx) return;

        // 停止当前播放
        this._stopCurrent();

        var source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.start(0);
        this.currentSource = source;
        this.isPlaying = true;
        this.nextPlayIndex = index + 1;

        var self = this;
        source.onended = function() {
            self.isPlaying = false;
            self.currentSource = null;
            // 播放下一个
            self._playNext();
        };
    },

    _playNext: function() {
        // 从队列中找下一个应播放的
        var nextIndex = -1;
        for (var i = 0; i < this.audioQueue.length; i++) {
            if (this.audioQueue[i].index === this.nextPlayIndex) {
                nextIndex = i;
                break;
            }
        }
        if (nextIndex >= 0) {
            var item = this.audioQueue.splice(nextIndex, 1)[0];
            this._playBuffer(item.buffer, item.index);
        }
    },

    _stopCurrent: function() {
        if (this.currentSource) {
            try {
                this.currentSource.stop();
            } catch (e) {}
            this.currentSource = null;
        }
        this.isPlaying = false;
    },

    // ── 中断 ──
    interrupt: function(newTurnId) {
        this._stopCurrent();
        this.audioQueue = [];
        this.nextPlayIndex = 0;
        this._generation = (this._generation || 0) + 1;
        this.turnId = newTurnId || 0;
    },

    // ── 控制 ──
    stop: function() {
        this._stopCurrent();
        this.audioQueue = [];
        this.nextPlayIndex = 0;
    },

    resume: function() {
        if (!this.isPlaying && this.audioQueue.length > 0) {
            this._playNext();
        }
    },

    // ── 清理 ──
    dispose: function() {
        this.stop();
        if (this.audioContext) {
            this.audioContext.close().catch(function() {});
            this.audioContext = null;
        }
    }
};

// 自动初始化
(function() {
    if (typeof AIGEME !== 'undefined') {
        TTSPlayer.init();
    }
})();

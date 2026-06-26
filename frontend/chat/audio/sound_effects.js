/**
 * SoundManager — 前端按钮和事件音效管理
 * 使用 Web Audio API 生成简单合成音效，无需外部音频文件
 */
class SoundManager {
    constructor() {
        this._enabled = true;
        this._volume = 0.5;
        this._audioCtx = null;
    }

    /** 启用/禁用音效 */
    set enabled(val) {
        this._enabled = val;
    }

    get enabled() {
        return this._enabled;
    }

    /** 获取或创建 AudioContext */
    _getContext() {
        if (!this._audioCtx) {
            try {
                this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            } catch (e) {
                console.warn('[SoundManager] Web Audio API 不可用:', e);
                return null;
            }
        }
        return this._audioCtx;
    }

    /**
     * 播放指定类型的音效
     * @param {string} type - 音效类型: 'button_click' | 'message_receive' | 'message_send' | 'tool_call' | 'turn_end' | 'error'
     */
    play(type) {
        if (!this._enabled) return;
        var ctx = this._getContext();
        if (!ctx) return;

        try {
            switch (type) {
                case 'button_click': this._playTone(ctx, 800, 0.08, 'sine'); break;
                case 'message_receive': this._playTone(ctx, 600, 0.12, 'sine'); break;
                case 'message_send': this._playTone(ctx, 1000, 0.1, 'sine'); break;
                case 'tool_call': this._playTone(ctx, 400, 0.15, 'triangle'); break;
                case 'turn_end': this._playTone(ctx, 500, 0.2, 'sine'); break;
                case 'error': this._playTone(ctx, 200, 0.3, 'sawtooth'); break;
                default: break;
            }
        } catch (e) {
            console.warn('[SoundManager] 音效播放失败:', e);
        }
    }

    /**
     * 播放一个简单音调
     * @param {AudioContext} ctx
     * @param {number} freq - 频率 Hz
     * @param {number} duration - 持续时间 秒
     * @param {OscillatorType} type - 波形类型
     */
    _playTone(ctx, freq, duration, type) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq, ctx.currentTime);
        gain.gain.setValueAtTime(this._volume, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + duration);
    }

    /** 预加载音效（Web Audio API 无需预加载） */
    preload() {
        // Web Audio API 懒初始化，无需预加载
    }

    /** 设置音量 0-1 */
    setVolume(level) {
        this._volume = Math.max(0, Math.min(1, level));
    }

    /** 清理资源 */
    dispose() {
        if (this._audioCtx) {
            this._audioCtx.close().catch(function() {});
            this._audioCtx = null;
        }
    }
}

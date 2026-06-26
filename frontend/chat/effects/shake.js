/**
 * ShakeManager — Live2D 表情震动效果
 * 在表情切换时对 #sprite-img 元素施加 CSS transform 震动动画
 */
const ShakeManager = {
    _presets: {
        'neutral':   { intensity: 0, duration: 0 },
        'happy':     { intensity: 6, duration: 200 },
        'excited':   { intensity: 10, duration: 250 },
        'surprised': { intensity: 12, duration: 300 },
        'angry':     { intensity: 16, duration: 350 },
        'sad':       { intensity: 4, duration: 400 },
        'thinking':  { intensity: 2, duration: 150 },
    },

    _animFrameId: null,
    _startTime: 0,
    _element: null,
    _intensity: 0,
    _duration: 0,
    _originalTransform: '',

    /**
     * 对目标元素执行震动动画
     * @param {HTMLElement} element - 目标元素（通常是 #sprite-img）
     * @param {string} expression - 表情名称
     */
    shake: function(element, expression) {
        this.stop(); // 停止正在进行的震动

        var preset = this._presets[expression] || this._presets['neutral'];
        this._intensity = preset.intensity;
        this._duration = preset.duration;

        if (this._intensity <= 0 || this._duration <= 0) return;

        this._element = element;
        this._originalTransform = element.style.transform || '';
        // 添加 will-change 优化性能
        element.style.willChange = 'transform';

        var self = this;
        // 延迟一帧以确保元素已渲染（解决立绘未加载时transform无效的问题）
        requestAnimationFrame(function() {
            if (self._element !== element) return; // 已被后续shake覆盖
            self._startTime = performance.now();
            self._animFrameId = requestAnimationFrame(function animate(currentTime) {
                var elapsed = currentTime - self._startTime;
                if (elapsed >= self._duration) {
                    self.stop();
                    return;
                }
                // 正弦波衰减震动（水平+垂直复合）
                var progress = elapsed / self._duration;
                var decay = 1 - progress; // 线性衰减
                var freq = 0.3;
                var offsetX = Math.sin(elapsed * freq) * self._intensity * decay;
                var offsetY = Math.sin(elapsed * freq * 1.7) * self._intensity * 0.6 * decay;
                element.style.transform = self._originalTransform + ' translate(' + offsetX + 'px, ' + offsetY + 'px)';
                self._animFrameId = requestAnimationFrame(animate);
            });
        });
    },

    /** 停止正在进行的震动 */
    stop: function() {
        if (this._animFrameId) {
            cancelAnimationFrame(this._animFrameId);
            this._animFrameId = null;
        }
        if (this._element) {
            this._element.style.transform = this._originalTransform;
            this._element.style.willChange = '';
            this._element = null;
        }
        this._intensity = 0;
        this._duration = 0;
    },
};

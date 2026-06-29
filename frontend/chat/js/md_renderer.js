/**
 * 纯正则 Markdown 渲染器 v2 — 无第三方依赖，无 DOM
 * 字符串级转义替代 DOM 转义，消除 tag 保护开销
 */

(function() {
    'use strict';

    // 字符串级 HTML 转义（不创建 DOM）
    function _escapeHtml(s) {
        if (!s) return '';
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /**
     * 将 Markdown 文本渲染为安全的 HTML
     *
     * 策略：先提取代码块（保护原始内容），然后处理行内标记，
     * 最后转义非标记部分的文本。不依赖 DOM，全部字符串操作。
     */
    function mdRender(text) {
        if (!text) return '';

        // ==== 阶段 1：提取代码块和行内代码 ====

        // 1a. 代码块: ```lang? code ``` → 占位符
        var codeBlocks = [];
        text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, function(m, lang, code) {
            var idx = codeBlocks.length;
            codeBlocks.push(code.trim());
            return '\x00CB' + idx + '\x00';
        });

        // 1b. 行内代码: `code` → 占位符
        var inlineCodes = [];
        text = text.replace(/`([^`]+)`/g, function(m, code) {
            var idx = inlineCodes.length;
            inlineCodes.push(code);
            return '\x00IC' + idx + '\x00';
        });

        // ==== 阶段 2：Markdown → HTML 标记（一行扫描） ====

        // 将文本按行拆分，逐行处理
        var lines = text.split('\n');
        var inUl = false;
        var inOl = false;
        var out = [];
        var paraLines = [];    // 收集普通文本行，按空行分组成段落

        function _flushPara() {
            if (paraLines.length > 0) {
                out.push('<p>' + paraLines.join('<br>') + '</p>');
                paraLines = [];
            }
        }

        function _closeLists() {
            if (inUl) { _flushPara(); out.push('</ul>'); inUl = false; }
            if (inOl) { _flushPara(); out.push('</ol>'); inOl = false; }
        }

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];

            // 空行 → 结束当前段落
            if (line.trim() === '') {
                _flushPara();
                continue;
            }

            // 引用: > text
            var qm = line.match(/^>\s?(.*)$/);
            if (qm) {
                _flushPara();
                _closeLists();
                out.push('<blockquote><p>' + qm[1] + '</p></blockquote>');
                continue;
            }

            // 无序列表: - item
            var um = line.match(/^-\s(.+)$/);
            if (um) {
                _flushPara();
                if (inOl) { out.push('</ol>'); inOl = false; }
                if (!inUl) { out.push('<ul>'); inUl = true; }
                out.push('<li>' + um[1] + '</li>');
                continue;
            }

            // 有序列表: 1. item
            var om = line.match(/^\d+\.\s(.+)$/);
            if (om) {
                _flushPara();
                if (inUl) { out.push('</ul>'); inUl = false; }
                if (!inOl) { out.push('<ol>'); inOl = true; }
                out.push('<li>' + om[1] + '</li>');
                continue;
            }

            // 普通文本行 — 若刚从列表过来，先关列表再入段
            _closeLists();
            paraLines.push(line);
        }
        _flushPara();
        if (inUl) out.push('</ul>');
        if (inOl) out.push('</ol>');
        text = out.join('\n');

        // ==== 阶段 3：行内标记处理（在完整文本上做 regex） ====

        // 图片: ![alt](url) → 占位符
        var images = [];
        text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function(m, alt, url) {
            var idx = images.length;
            images.push('<img src="' + url.replace(/"/g, '&quot;') + '" alt="' + _escapeHtml(alt) + '">');
            return '\x00IMG' + idx + '\x00';
        });

        // 链接: [text](url) → 占位符
        var links = [];
        text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(m, label, url) {
            var idx = links.length;
            links.push('<a href="' + url.replace(/"/g, '&quot;') + '" target="_blank" rel="noopener">' + _escapeHtml(label) + '</a>');
            return '\x00LNK' + idx + '\x00';
        });

        // 粗体: **text**
        text = text.replace(/\*\*([^*]+)\*\*/g, function(m, inner) {
            return '<strong>' + _escapeHtml(inner) + '</strong>';
        });

        // 斜体: *text*（避免匹配 **；且 * 前后不能有空格，避免将 * ... * 列表标记误判为斜体）
        text = text.replace(/(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)/g, function(m, inner) {
            return '<em>' + _escapeHtml(inner) + '</em>';
        });

        // 恢复图片占位符
        text = text.replace(/\x00IMG(\d+)\x00/g, function(m, idx) { return images[+idx]; });
        // 恢复链接占位符
        text = text.replace(/\x00LNK(\d+)\x00/g, function(m, idx) { return links[+idx]; });

        // ==== 阶段 4：转义非标记文本，清理块间换行 ====

        // 先将完整文本基于 HTML 标签分割，只转义非标签部分
        var parts = text.split(/(<[^>]+>)/);
        for (var p = 0; p < parts.length; p++) {
            // 不以 < 开头 → 普通文本 → 转义
            if (parts[p].charAt(0) !== '<') {
                parts[p] = _escapeHtml(parts[p]);
            }
            // 以 < 开头 → 已经是标签 → 不转义
        }
        // 重建文本并清理块间残留换行（段落用 <p>，行内换行已用 <br>，不需要额外 \n）
        text = parts.join('').replace(/\n/g, '');

        // ==== 阶段 5：恢复代码内容 ====

        text = text.replace(/\x00IC(\d+)\x00/g, function(m, i) {
            return '<code>' + _escapeHtml(inlineCodes[+i]) + '</code>';
        });
        text = text.replace(/\x00CB(\d+)\x00/g, function(m, i) {
            return '<pre><code>' + _escapeHtml(codeBlocks[+i]) + '</code></pre>';
        });

        return text;
    }

    window.mdRender = mdRender;
})();

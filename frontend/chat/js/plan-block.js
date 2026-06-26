/**
 * PlanCardRenderer — 计划卡片渲染器
 * 负责将 plan/plan_progress/plan_review block 渲染为可交互卡片
 *
 * 状态机:
 *   IDLE → PLANNING → PLAN_SHOW → EXECUTING → COMPLETED
 *                                              ↘ FAILED
 *                          ↘ REVIEWING → EXECUTING
 */

class PlanCardRenderer {
    constructor(messageEl) {
        this.messageEl = messageEl;
        this.cardEl = null;
        this.state = 'IDLE';
        this.plan = null;
        this.subtaskStates = {};
        this.subtaskSummaries = {};
        this._stopBtn = null;
    }

    /**
     * 处理 Block 更新
     * 由 blocks.js 的 handleBlock() 调用
     */
    handleBlock(block) {
        switch (block.block_type) {
            case 'plan_thinking':
                return this._handlePlanning(block);
            case 'plan':
                return this._handlePlan(block);
            case 'plan_progress':
                return this._handleProgress(block);
            case 'plan_review':
                return this._handleReview(block);
            default:
                break;
        }
    }

    // ── 核心渲染方法 ──

    _handlePlanning(block) {
        // 规划中：显示 "📋 正在制定计划..." + 流式文本
        this.state = 'PLANNING';
        if (!this.cardEl) {
            this.cardEl = this._createCard();
            this.messageEl.appendChild(this.cardEl);
        }
        this._updateThinkingText(block.delta || '');
    }

    _handlePlan(block) {
        // 计划生成完毕：渲染步骤列表
        this.state = 'PLAN_SHOW';
        const data = typeof block.delta === 'string'
            ? JSON.parse(block.delta)
            : block.delta;
        this.plan = data;
        this._renderPlanList();
    }

    _handleProgress(block) {
        // 子任务进度更新：实时更新状态图标和进度条
        const data = typeof block.delta === 'string'
            ? JSON.parse(block.delta)
            : block.delta;

        // 更新子任务状态
        if (data.subtask_id) {
            this.subtaskStates[data.subtask_id] = data.status;
            if (data.summary) {
                this.subtaskSummaries[data.subtask_id] = data.summary;
            }
            this._updateStepStatus(data.subtask_id, data);
        }

        // 更新进度条
        if (data.completed !== undefined && data.total !== undefined) {
            this._updateProgressBar(data.completed, data.total);
        }

        // 根据子任务类型切换状态
        if (data.type === 'subtask_failed') {
            this.state = 'FAILED';
        } else if (data.type === 'subtask_done' || data.type === 'subtask_start') {
            this.state = 'EXECUTING';
        } else if (data.type === 'plan_progress' && data.status === 'completed') {
            this.state = 'COMPLETED';
        }
    }

    _handleReview(block) {
        // 审核请求：显示按钮
        this.state = 'REVIEWING';
        const data = typeof block.delta === 'string'
            ? JSON.parse(block.delta)
            : block.delta;
        this._renderReviewButtons(data);
    }

    _createCard() {
        const card = document.createElement('div');
        card.className = 'plan-card';
        card.innerHTML = `
            <div class="plan-card-header">
                <span class="plan-icon">📋</span>
                <span class="plan-title">执行计划</span>
                <button class="plan-toggle">▼</button>
            </div>
            <div class="plan-card-body">
                <div class="plan-thinking" style="color:#888;font-size:13px;font-style:italic;">正在分析任务并制定计划...</div>
            </div>
        `;
        // 展开/收起交互
        card.querySelector('.plan-toggle').addEventListener('click', () => {
            card.classList.toggle('collapsed');
        });
        return card;
    }

    _renderPlanList() {
        const body = this.cardEl.querySelector('.plan-card-body');
        if (!this.plan) return;

        let html = '';

        // 目标
        if (this.plan.goal) {
            html += `<div class="plan-goal">目标：${this._escapeHtml(this.plan.goal)}</div>`;
        }

        // 策略
        if (this.plan.strategy) {
            html += `<div class="plan-strategy">策略：${this._escapeHtml(this.plan.strategy)}</div>`;
        }

        // 步骤列表
        html += '<div class="plan-steps">';
        const subtasks = this.plan.subtasks || [];
        for (const st of subtasks) {
            const icon = this._getStatusIcon(this.subtaskStates[st.id] || 'pending');
            const deps = st.depends_on && st.depends_on.length > 0
                ? `<span class="plan-dependency">→ ${st.depends_on.join(', ')}</span>`
                : '';
            html += `
                <div class="plan-step" data-subtask-id="${st.id}">
                    <span class="step-icon">${icon}</span>
                    <span class="step-title">${this._escapeHtml(st.title)} ${deps}</span>
                    <span class="step-detail" style="display:none"></span>
                </div>
            `;
            this.subtaskStates[st.id] = this.subtaskStates[st.id] || 'pending';
        }
        html += '</div>';

        // 进度条
        html += `<div class="plan-progress-bar">
            <div class="progress-fill" style="width: 0%"></div>
            <span class="progress-text">0/${subtasks.length}</span>
        </div>`;

        body.innerHTML = html;
    }

    _updateStepStatus(subtaskId, data) {
        const stepEl = this.cardEl.querySelector(`[data-subtask-id="${subtaskId}"]`);
        if (!stepEl) return;

        // 状态图标映射
        const iconMap = {
            pending: '⏳',
            running: '🔄',
            completed: '✅',
            failed: '❌',
            skipped: '⏭️',
        };
        const iconEl = stepEl.querySelector('.step-icon');
        if (iconEl) {
            iconEl.textContent = iconMap[data.status] || '⏳';
        }

        // 完成时显示摘要
        if (data.summary) {
            const detailEl = stepEl.querySelector('.step-detail');
            if (detailEl) {
                detailEl.textContent = data.summary;
                detailEl.style.display = 'block';
            }
        }

        // 当前运行步骤高亮
        stepEl.classList.toggle('active', data.status === 'running');
    }

    _updateProgressBar(completed, total) {
        const fill = this.cardEl.querySelector('.progress-fill');
        const text = this.cardEl.querySelector('.progress-text');
        const pct = total > 0 ? (completed / total * 100) : 0;
        if (fill) fill.style.width = pct + '%';
        if (text) text.textContent = `${completed}/${total}`;
    }

    _updateThinkingText(text) {
        const thinkingEl = this.cardEl.querySelector('.plan-thinking');
        if (thinkingEl) {
            thinkingEl.textContent = text || '正在分析任务并制定计划...';
        }
    }

    _renderReviewButtons(data) {
        const body = this.cardEl.querySelector('.plan-card-body');
        if (!body) return;

        const existing = body.querySelector('.plan-review-actions');
        if (existing) existing.remove();

        const actions = document.createElement('div');
        actions.className = 'plan-review-actions';
        actions.innerHTML = `
            <button class="btn-approve" data-action="approve">✅ 执行计划</button>
            <button class="btn-reject" data-action="reject">❌ 取消</button>
        `;

        // 添加按钮事件
        actions.querySelector('.btn-approve').addEventListener('click', () => {
            this._sendPlanAction('approve');
        });
        actions.querySelector('.btn-reject').addEventListener('click', () => {
            this._sendPlanAction('reject');
        });

        body.appendChild(actions);
    }

    _sendPlanAction(action) {
        if (window.AIGEME && AIGEME.shared && AIGEME.shared.ws) {
            const msg = {
                type: 'plan_action',
                action: action,
            };
            if (this.plan && this.plan.plan_id) {
                msg.plan_id = this.plan.plan_id;
            }
            try {
                AIGEME.shared.ws.send(JSON.stringify(msg));
            } catch (e) {
                console.error('[plan_action] 发送失败:', e);
            }
        }
    }

    _getStatusIcon(status) {
        const icons = {
            pending: '⏳',
            running: '🔄',
            completed: '✅',
            failed: '❌',
            skipped: '⏭️',
        };
        return icons[status] || '⏳';
    }

    _escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// 导出到全局（供 blocks.js 使用）
if (typeof window !== 'undefined') {
    window.PlanCardRenderer = PlanCardRenderer;
}

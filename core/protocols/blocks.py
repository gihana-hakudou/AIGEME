"""Block 协议消息 Pydantic 模型定义"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Block 类型枚举
BlockType = Literal[
    "thinking",      # Agent 推理过程
    "speech",        # 角色对话文本
    "expression",    # 立绘表情切换
    "tool_call",     # 工具调用通知
    "tool_result",   # 工具执行结果
    "scene",         # 场景/背景切换
    "narration",     # 旁白/描述
    "choice",        # 选项分支（预留）
    "bgm",           # 背景音乐控制
    "emotion",       # 情感状态更新
    "system",        # 系统消息
    "turn_end",      # 本轮结束信号
    "error",         # 错误信息
    "confirm",       # 用户确认对话框
    "memory_update", # 记忆已被修改，前端需刷新记忆面板
    "workspace_update", # 工作区文件已变更，前端需刷新工作区面板
    # PAE Plan-and-Execute 相关
    "plan_thinking",  # 规划进度（流式）
    "plan",           # 完整计划
    "plan_progress",  # 子任务进度更新
    "plan_review",    # 计划审核请求
    # TTS 相关
    "audio",          # TTS 合成音频数据
    "audio_play_end", # 前端通知后端音频播放结束
    "tts_state",      # TTS 状态同步
]


class Block(BaseModel):
    """WebSocket 服务端 → 客户端消息"""

    type: str = Field(default="block", description="消息类型标识")
    block_type: BlockType = Field(description="Block 具体类型")
    delta: str = Field(default="", description="内容片段（可能是完整的，也可能是流式片段）")
    is_final: bool = Field(default=True, description="是否是该 Block 的最后一段")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")

    model_config = {"frozen": True}


class ClientMessage(BaseModel):
    """WebSocket 客户端 → 服务端消息"""

    type: Literal["user_message", "ping", "disconnect", "cancel", "plan_action"] = Field(description="消息类型")
    content: Optional[str] = Field(default=None, description="消息文本")
    character_id: str = Field(default="", description="目标角色 ID（user_message 类型需要）")
    mode: str = Field(default="single", description="对话模式 (single/group)")
    images: list[str] = Field(default_factory=list, description="图片 base64 列表")
    stream: bool = Field(default=True, description="流式开关（false=禁用流式输出）")
    tts_enabled: bool = Field(default=False, description="TTS 语音开关")
    tts_mode: str = Field(default="preset", description="TTS 模式 (preset/voice_design/voice_clone)")
    tts_voice: str = Field(default="冰糖", description="TTS 音色")
    tts_tone: str = Field(default="自然温和", description="TTS 默认语气")
    tts_voice_design_prompt: str = Field(default="", description="TTS 音色描述（voice_design 模式）")
    tts_voice_clone_style_desc: str = Field(default="", description="TTS 语音克隆风格描述（voice_clone 模式）")
    confirm_action: str = Field(default="", description="确认操作类型")
    tool_call_id: str = Field(default="", description="工具调用的原始 ID")
    plan_action: str = Field(default="", description="计划审核操作 (approve/reject)")

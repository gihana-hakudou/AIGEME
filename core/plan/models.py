"""PAE 数据结构：SubTask, Plan, PlanResponse"""

from enum import Enum

from pydantic import BaseModel, Field

# 绝对硬限制：任何计划都不能超过此子任务数
MAX_SUBTASKS_HARD_LIMIT = 100


class SubTaskStatus(str, Enum):
    """子任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubTask(BaseModel):
    """子任务（Pydantic BaseModel — 兼容 instructor 结构化输出）"""
    id: str = Field(description='子任务 ID，如 "sub_1"')
    title: str = Field(description="简短标题")
    description: str = Field(description="详细描述，传给 Executor 的用户消息")
    depends_on: list[str] = Field(default_factory=list, description="依赖的子任务 ID")
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str | None = None
    result_file: str | None = None
    context_hint: str | None = None
    error: str | None = None

    class Config:
        use_enum_values = True


class Plan:
    """执行计划（运行时数据结构，不用作 instructor response_model）"""
    goal: str
    subtasks: list[SubTask]
    strategy: str | None = None

    def __init__(self, goal: str, subtasks: list[SubTask], strategy: str | None = None) -> None:
        self.goal = goal
        self.subtasks = subtasks
        self.strategy = strategy

    @property
    def progress(self) -> tuple[int, int]:
        """(已完成数, 总数)"""
        done = sum(1 for s in self.subtasks if s.status == SubTaskStatus.COMPLETED)
        return (done, len(self.subtasks))

    @property
    def all_done(self) -> bool:
        return all(
            s.status in (SubTaskStatus.COMPLETED, SubTaskStatus.SKIPPED)
            for s in self.subtasks
        )


class PlanResponse(BaseModel):
    """LLM 规划输出（Pydantic BaseModel — 供 instructor 结构化解析）"""
    reasoning: str = Field(default="", description="规划思路")
    goal: str = Field(default="", description="确认的主目标")
    strategy: str = Field(default="", description="执行策略说明")
    subtasks: list[SubTask] = Field(default_factory=list, description="子任务列表")

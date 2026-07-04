"""对话历史 API 路由"""

import json
import logging

from fastapi import APIRouter

from core.config.settings import get_config
from core.utils import PROJECT_ROOT

logger = logging.getLogger(__name__)
router = APIRouter(tags=["conversations"])


@router.get("/api/conversations/{character_id}")
async def list_conversations(character_id: str) -> list[dict]:
    """列出指定角色的历史会话摘要"""
    data_dir = PROJECT_ROOT / ".AIGEME" / ".data"
    user_id = get_config().get("user", {}).get("default_id", "local")
    conv_dir = data_dir / user_id / character_id / "conversations"
    results = []
    if conv_dir.exists():
        # 所有文件按时间合并：分卷(_001→_002)在前，主文件(conversations.json)在后
        files = sorted(conv_dir.glob("*.json"))
        split_files = sorted(f for f in files if "_" in f.stem)
        main_files = [f for f in files if "_" not in f.stem]
        for f in split_files + main_files:
            try:
                records = json.loads(f.read_text("utf-8"))
                if not records:
                    continue
                last_msg = records[-1].get("data", {}).get("content", "") if records else ""
                if results:
                    results[0]["message_count"] += len(records)
                    results[0]["last_message"] = last_msg[:100]
                    results[0]["timestamp"] = records[-1].get("timestamp", "")
                else:
                    results.append({
                        "date": "all",
                        "message_count": len(records),
                        "last_message": last_msg[:100],
                        "timestamp": records[-1].get("timestamp", "") if records else "",
                    })
            except (json.JSONDecodeError, OSError):
                continue
    return results


@router.get("/api/conversations/{character_id}/{date}")
async def get_conversation(character_id: str, date: str = "all") -> list[dict]:
    """获取完整对话记录（所有文件合并，忽略日期参数）"""
    data_dir = PROJECT_ROOT / ".AIGEME" / ".data"
    user_id = get_config().get("user", {}).get("default_id", "local")
    conv_dir = data_dir / user_id / character_id / "conversations"
    all_records = []
    files = sorted(conv_dir.glob("*.json"))
    # 分卷在前，主文件在后，保证时间顺序
    split_files = sorted(f for f in files if "_" in f.stem)
    main_files = [f for f in files if "_" not in f.stem]
    for f in split_files + main_files:
        try:
            records = json.loads(f.read_text("utf-8"))
            all_records.extend(records)
        except (json.JSONDecodeError, OSError):
            continue
    return all_records

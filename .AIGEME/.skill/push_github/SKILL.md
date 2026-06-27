---
name: push_to_github
description: 通过 GitHub API 推送文件到仓库。支持大文件、批量文件、自动获取 token。
version: 1.0.0
---

# GitHub Push 脚本

## 用法

```bash
cd G:\设计\AIGEME
git push_api <文件路径1> <文件路径2> ... -m "提交信息"
```

或者通过 Python 直接调用：

```python
from scripts.push_github import push_files
push_files(
    files=[{"path": "core/main.py", "content": open("core/main.py").read()}],
    message="提交信息"
)
```

## 脚本

- `scripts/push_github.py` — 主脚本
- 自动从 `local.yaml` 的 `env.GITHUB_TOKEN` 读取 token
- 支持任意数量文件，自动分批

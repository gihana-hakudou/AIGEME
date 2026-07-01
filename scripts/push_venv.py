#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
推送 venv 文件夹到 GitHub 仓库
"""

import os
import sys
from pathlib import Path
from github import Github

def push_venv_to_github(repo_owner, repo_name, token):
    """
    推送 venv 文件夹到 GitHub 仓库
    
    Args:
        repo_owner: 仓库所有者
        repo_name: 仓库名称
        token: GitHub Personal Access Token
    """
    # 初始化 GitHub 客户端
    g = Github(token)
    repo = g.get_repo(f"{repo_owner}/{repo_name}")
    
    # 检查 venv 文件夹是否存在
    venv_path = Path("venv")
    if not venv_path.exists():
        print("错误: venv 文件夹不存在")
        return False
    
    # 获取 venv 文件夹内容
    venv_files = []
    for root, dirs, files in os.walk(venv_path):
        for file in files:
            file_path = Path(root) / file
            # 计算相对于 venv 的路径
            rel_path = file_path.relative_to(venv_path)
            venv_files.append(str(rel_path))
    
    print(f"找到 {len(venv_files)} 个文件需要推送")
    
    # 推送文件
    for file_path in venv_files:
        full_path = venv_path / file_path
        try:
            with open(full_path, 'rb') as f:
                content = f.read()
            
            # 检查文件是否已存在
            try:
                repo.get_contents(f"venv/{file_path}")
                # 文件已存在，更新
                repo.update_file(
                    f"venv/{file_path}",
                    f"Update venv file: {file_path}",
                    content,
                    repo.get_contents(f"venv/{file_path}").sha
                )
                print(f"更新文件: venv/{file_path}")
            except:
                # 文件不存在，创建
                repo.create_file(
                    f"venv/{file_path}",
                    f"Add venv file: {file_path}",
                    content
                )
                print(f"创建文件: venv/{file_path}")
                
        except Exception as e:
            print(f"处理文件 {file_path} 时出错: {e}")
            continue
    
    print("venv 文件夹推送完成")
    return True

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("用法: python scripts/push_venv.py <repo_owner> <repo_name> <token>")
        sys.exit(1)
    
    repo_owner = sys.argv[1]
    repo_name = sys.argv[2]
    token = sys.argv[3]
    
    push_venv_to_github(repo_owner, repo_name, token)
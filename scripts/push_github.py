"""
GitHub Push 脚本 — 通过 Git API 推送文件到仓库

用法：
  python scripts/push_github.py <文件路径1> <文件路径2> ... -m "提交信息"

参数：
  -m / --message   必填，提交信息
  -o / --owner     仓库所有者（默认从 git remote 或 mcp 配置读取）
  -r / --repo      仓库名（同上）
  -b / --branch    分支名（默认 main）
  -t / --token     GitHub Personal Access Token（默认从 mcp 配置或环境变量读取）
  --dry-run        只打印要推的文件，不实际推送

示例：
  python scripts/push_github.py core/main.py frontend/chat/js/app.js -m "fix: 立绘路径修复"
  python scripts/push_github.py .AIGEME/.skill/push_github/SKILL.md -m "feat: 新增推送技能"
"""

import argparse
import base64
import json
import os
import sys
import urllib.request


def get_token():
    """按优先级获取 GitHub token"""
    # 1. 环境变量
    for var in ['GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_PERSONAL_ACCESS_TOKEN']:
        token = os.environ.get(var)
        if token:
            return token

    # 2. MCP 配置
    mcp_config_paths = [
        '.AIGEME/mcp-servers/mcp-servers.json',
        '.AIGEME/mcp.json',
    ]
    for p in mcp_config_paths:
        if os.path.exists(p):
            try:
                data = json.load(open(p, encoding='utf-8'))
                servers = data.get('servers', {})
                for sid, srv in servers.items():
                    env = srv.get('config', {}).get('stdio', {}).get('env', {})
                    for key in ['GITHUB_PERSONAL_ACCESS_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN']:
                        if env.get(key):
                            return env[key]
            except Exception:
                pass

    return None


def get_remote_info():
    """从 git remote 读取 owner/repo"""
    import subprocess
    try:
        r = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, timeout=5
        )
        url = r.stdout.strip()
        # git@github.com:gihana-hakudou/AIGEME.git
        # https://github.com/gihana-hakudou/AIGEME.git
        if 'github.com' in url:
            parts = url.replace('git@github.com:', '').replace('https://github.com/', '').rstrip('.git').split('/')
            if len(parts) >= 2:
                return parts[0], parts[1]
    except Exception:
        pass
    return 'gihana-hakudou', 'AIGEME'


def api_call(url, data=None, method='GET', token=None):
    """通用 GitHub API 调用"""
    headers = {
        'Authorization': f'token {token}',
        'User-Agent': 'aigeme-push/1.0',
        'Accept': 'application/vnd.github.v3+json',
    }
    if data is not None:
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f'HTTP {e.code}: {e.reason}')
        print(f'Response: {body[:500]}')
        sys.exit(1)


def push_files(files, message, owner='gihana-hakudou', repo='AIGEME',
               branch='main', token=None, dry_run=False):
    """
    推送文件到 GitHub 仓库

    参数：
        files: list[{"path": str, "content": str}]
        message: str — 提交信息
        owner/repo/branch: 仓库信息
        token: str — GitHub token，不传则自动获取
        dry_run: bool — 只打印不推送

    返回：提交 SHA
    """
    if token is None:
        token = get_token()
    if not token:
        print('错误：找不到 GitHub token')
        print('请设置环境变量 GITHUB_TOKEN，或通过 -t 参数传入')
        sys.exit(1)

    print(f'目标仓库: {owner}/{repo} @ {branch}')
    print(f'文件数量: {len(files)}')
    if dry_run:
        for f in files:
            size = len(f['content'].encode('utf-8'))
            print(f'  [dry-run] {f["path"]} ({size} bytes)')
        print('dry-run 模式，未实际推送')
        return None

    api_base = f'https://api.github.com/repos/{owner}/{repo}'

    # 1. 获取最新 commit
    print('获取最新 commit...')
    ref_data = api_call(f'{api_base}/git/refs/heads/{branch}', token=token)
    latest_sha = ref_data['object']['sha']
    print(f'  最新 commit: {latest_sha[:12]}')

    # 2. 获取 base tree
    commit_data = api_call(f'{api_base}/git/commits/{latest_sha}', token=token)
    base_tree_sha = commit_data['tree']['sha']
    print(f'  基础 tree: {base_tree_sha[:12]}')

    # 3. 为每个文件创建 blob
    blobs = []
    for i, f in enumerate(files):
        content_bytes = f['content'].encode('utf-8')
        encoded = base64.b64encode(content_bytes).decode('ascii')
        print(f'  创建 blob [{i+1}/{len(files)}]: {f["path"]} ({len(content_bytes)} bytes)')

        blob_data = api_call(
            f'{api_base}/git/blobs',
            data=json.dumps({'content': encoded, 'encoding': 'base64'}).encode('utf-8'),
            method='POST',
            token=token
        )
        blobs.append({
            'path': f['path'],
            'mode': '100644',
            'type': 'blob',
            'sha': blob_data['sha'],
        })

    # 4. 创建 tree
    print('创建新 tree...')
    tree_data = api_call(
        f'{api_base}/git/trees',
        data=json.dumps({'base_tree': base_tree_sha, 'tree': blobs}).encode('utf-8'),
        method='POST',
        token=token
    )
    new_tree_sha = tree_data['sha']
    print(f'  新 tree: {new_tree_sha[:12]}')

    # 5. 创建 commit
    print('创建 commit...')
    commit_result = api_call(
        f'{api_base}/git/commits',
        data=json.dumps({
            'message': message,
            'tree': new_tree_sha,
            'parents': [latest_sha],
        }).encode('utf-8'),
        method='POST',
        token=token
    )
    new_commit_sha = commit_result['sha']
    print(f'  新 commit: {new_commit_sha[:12]}')

    # 6. 更新分支引用
    print('更新分支引用...')
    api_call(
        f'{api_base}/git/refs/heads/{branch}',
        data=json.dumps({'sha': new_commit_sha, 'force': False}).encode('utf-8'),
        method='PATCH',
        token=token
    )
    print(f'✅ 推送成功！commit: {new_commit_sha}')
    return new_commit_sha


def main():
    parser = argparse.ArgumentParser(description='推送文件到 GitHub')
    parser.add_argument('files', nargs='+', help='要推送的文件路径')
    parser.add_argument('-m', '--message', required=True, help='提交信息')
    parser.add_argument('-o', '--owner', help='仓库所有者')
    parser.add_argument('-r', '--repo', help='仓库名')
    parser.add_argument('-b', '--branch', default='main', help='分支名')
    parser.add_argument('-t', '--token', help='GitHub token')
    parser.add_argument('--dry-run', action='store_true', help='只预览不推送')
    args = parser.parse_args()

    # 自动识别 owner/repo
    owner, repo = args.owner, args.repo
    if not owner or not repo:
        default_owner, default_repo = get_remote_info()
        owner = owner or default_owner
        repo = repo or default_repo

    # 读取文件内容
    files = []
    for path in args.files:
        if not os.path.exists(path):
            print(f'⚠ 文件不存在，跳过: {path}')
            continue
        content = open(path, 'r', encoding='utf-8').read()
        files.append({'path': path.replace('\\', '/'), 'content': content})

    if not files:
        print('没有文件可推送')
        sys.exit(1)

    push_files(files, args.message, owner, repo, args.branch, args.token, args.dry_run)


if __name__ == '__main__':
    main()

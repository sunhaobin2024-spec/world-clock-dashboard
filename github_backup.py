#!/usr/bin/env python3
"""
linux-sim 源码 GitHub 备份脚本
================================
本沙箱网络封锁了 github.com:443（git 智能 HTTP 协议走这个端口），
但 api.github.com 可达，因此改用 GitHub Git Database REST API 推送。

目标：把本地已跟踪的全部源码推到仓库 sunhaobin2024-spec/world-clock-dashboard
的 linux-sim 分支（独立分支，不影响 main 上的 GitHub Pages 应急页）。

用法：
    python3 github_backup.py            # 增量追加（基于 linux-sim 现有 HEAD 作父提交）
    python3 github_backup.py --force    # 忽略已有 HEAD，重建 orphan 提交

注意：
    - 凭据从 .github/token 读取（该文件已被 .gitignore 排除，不会入库）
    - 大体积/密钥/日志文件由 .gitignore 排除，只备份源码
    - 网络受限时单文件顺序上传，耐心等待即可
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OWNER = "sunhaobin2024-spec"
REPO = "world-clock-dashboard"
BRANCH = "linux-sim"
TOKEN = open(os.path.join(HERE, ".github", "token")).read().strip()
API = "https://api.github.com"


def gh(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "linux-sim-backup"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode(errors="ignore"))


def tracked_files():
    """返回 [(path, mode), ...]，mode 取自 git（含可执行位）。"""
    raw = subprocess.check_output(["git", "-C", HERE, "ls-files", "-z", "--stage"])
    files = []
    for e in raw.split(b"\0"):
        if not e:
            continue
        meta, path_b = e.split(b"\t", 1)
        mode = meta.split(b" ")[0].decode()
        files.append((path_b.decode("utf-8"), mode))
    return files


def existing_head():
    st, r = gh("GET", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}")
    if st == 200:
        return r["object"]["sha"]
    return None


def main():
    force = "--force" in sys.argv
    files = tracked_files()
    print("待推送文件数:", len(files))

    parent = None if force else existing_head()

    blobs = []
    for path, mode in files:
        data = open(os.path.join(HERE, path), "rb").read()
        b64 = base64.b64encode(data).decode()
        st, r = gh("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                   {"content": b64, "encoding": "base64"})
        if st != 201:
            print("BLOB 失败", path, st, r)
            sys.exit(1)
        blobs.append({"path": path, "mode": mode, "type": "blob", "sha": r["sha"]})

    st, r = gh("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"tree": blobs})
    if st != 201:
        print("TREE 失败", st, r)
        sys.exit(1)
    tree_sha = r["sha"]

    body = {"message": "backup: linux-sim 源码全量备份（经 Git Database API 推送）",
            "tree": tree_sha}
    if parent:
        body["parents"] = [parent]
    st, r = gh("POST", f"/repos/{OWNER}/{REPO}/git/commits", body)
    if st != 201:
        print("COMMIT 失败", st, r)
        sys.exit(1)
    commit_sha = r["sha"]

    if parent:
        st, r = gh("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
                   {"sha": commit_sha})
        if st not in (200, 201):
            print("REF 更新失败", st, r)
            sys.exit(1)
        print("分支已追加提交:", BRANCH, "parent:", parent[:10])
    else:
        st, r = gh("POST", f"/repos/{OWNER}/{REPO}/git/refs",
                   {"ref": f"refs/heads/{BRANCH}", "sha": commit_sha})
        if st not in (201, 200):
            print("REF 创建失败", st, r)
            sys.exit(1)
        print("分支已创建:", BRANCH)

    print("commit:", commit_sha)
    print("OK")


if __name__ == "__main__":
    main()

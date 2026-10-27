#!/usr/bin/env python3
"""
cloudsystem 应急页自动同步脚本（GitHub Pages 版）
仓库已固定：sunhaobin2024-spec/world-clock-dashboard
用法：
  python3 sync_emergency.py            # 重新生成应急页 → 有变化才推送到 GitHub
  python3 sync_emergency.py --check    # 只检查差异，不推送
  python3 sync_emergency.py --force    # 强制推送（无论是否变化）
流程：
  1) 从 changelog.js 提取当前版本号与最新一条更新记录，注入应急页公告区
  2) GitHub Contents API：GET 现有 index.html（拿到 sha 与内容）
  3) 内容有变化（或 --force）才 PUT 新文件，否则跳过，避免空提交
以后每次给完整版发版后跑一次本脚本，应急页即同步更新。
"""
import base64, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GH_TOKEN_PATH = os.path.join(HERE, ".github", "token")
OWNER = "sunhaobin2024-spec"
REPO = "world-clock-dashboard"
PAGE = os.path.join(HERE, "emergency", "index.html")
MAIN_SITE = "https://3920850391.dpdns.org/"


def gh(method, path, token, body=None):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "cloudsystem-sync"})
    try:
        r = urllib.request.urlopen(req, timeout=40)
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


def regenerate_page():
    """把版本号/最新更新记录注入应急页公告区（幂等替换标记之间的内容）。"""
    s = open(PAGE, encoding="utf-8").read()
    cl = open(os.path.join(HERE, "changelog.js"), encoding="utf-8").read()
    m = re.search(r'window\.CURRENT_VERSION = "([^"]+)"', cl)
    ver = m.group(1) if m else "?"
    items = []
    mm = re.search(r'ver:"' + re.escape(ver) + r'".*?items:\[(.*?)\]', cl, re.S)
    if mm:
        items = re.findall(r'"([^"]+)"', mm.group(1))[:2]
    brief = "；".join(x[:40] for x in items) or "见完整版更新记录"
    notice = ("⚠️ 这是 <b>应急备用页</b>（GitHub 托管，纯本地运行）。完整版当前版本 <b>%s</b>（%s）。"
              "主站恢复后请回 <a href=\"%s\" style=\"color:var(--acc)\">%s</a>。"
              % (ver, brief, MAIN_SITE, "3920850391.dpdns.org"))
    s = re.sub(r'<div class="notice">.*?</div>',
               '<div class="notice">' + notice + "</div>", s, count=1, flags=re.S)
    open(PAGE, "w", encoding="utf-8").write(s)
    return ver


def main():
    check = "--check" in sys.argv
    force = "--force" in sys.argv
    if not os.path.exists(GH_TOKEN_PATH):
        print("缺少 GitHub token：请把 token 存到 .github/token（内容为 token 本身）")
        sys.exit(1)
    token = open(GH_TOKEN_PATH).read().strip()

    ver = regenerate_page()
    print("应急页已注入版本信息：", ver)
    local_b64 = base64.b64encode(open(PAGE, "rb").read()).decode()

    code, old = gh("GET", f"/repos/{OWNER}/{REPO}/contents/index.html", token)
    remote_b64 = old.get("content", "").replace("\n", "") if code == 200 else None
    remote_sha = old.get("sha") if code == 200 else None

    if code == 404:
        print("远程暂无 index.html，将创建。")
        changed = True
    elif remote_b64 == local_b64:
        print("远程内容已是最新，无需推送。")
        changed = False
    else:
        print("检测到内容变化，准备更新。")
        changed = True

    if check:
        print("[--check] 未推送。")
        return
    if not changed and not force:
        return

    body = {"message": "sync emergency page " + ver, "content": local_b64}
    if remote_sha:
        body["sha"] = remote_sha
    code, r = gh("PUT", f"/repos/{OWNER}/{REPO}/contents/index.html", token, body)
    if code in (200, 201):
        print("推送 index.html：OK")
        print("页面地址：https://%s.github.io/%s/" % (OWNER, REPO))
    else:
        print("推送失败：", r)


if __name__ == "__main__":
    main()

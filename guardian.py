#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""linux-sim 24 小时自愈守护。

为什么要重写而不是继续用 bash 版 watchdog.sh：
  bash 版只做一件事 —— `ss -ltn | grep 8080`，也就是"端口还在不在"。
  端口在 ≠ 服务能用。实际会出现的几种"活着但不能用"：
    · 线程池被慢请求占满，端口照听，但所有请求都超时
    · 内存泄漏到 1.9G，RLIMIT_AS 触发后 exec 全部报错
    · cloudflared 挂了 —— 后端完全健康，但公网 502（bash 版完全看不见）
    · 隧道 URL 换了 —— 后端健康、公网也通，但你手机上存的链接全废
  这四种 bash 版一个都发现不了。

本守护的检查项（每轮全跑，耗时毫秒级）：
  1. 后端进程在不在
  2. 后端真能不能干活（HTTP /login 探活 + 响应时间）
  3. 隧道进程在不在
  4. 隧道 URL 变没变（变了要立刻记下来，这是最容易被忽略的故障）
  5. 内存水位（超阈值主动重启，止损比等 OOM 快）
  6. 日志/会话文件体积（防撑爆磁盘）
  7. 密钥文件完整性（被误删要能从备份恢复，否则你会发现自己进不去）
  8. 白名单完整性（被清空要能从备份恢复）

设计原则：宁可多重启一次，也不要"看着在运行其实不能用"。
所有自愈动作都写审计，事后能查清是谁在什么时候动的手。
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8080
INTERVAL = 10                      # 巡检间隔（秒）
BASE = f"http://127.0.0.1:{PORT}"

SRV_LOG = "/tmp/srv.log"
GUARD_LOG = os.path.join(HERE, ".guardian.log")
AUDIT_LOG = os.path.join(HERE, ".audit.log")
URL_FILE = os.path.join(HERE, ".public_url")
URL_BACKUP = os.path.join(HERE, ".public_url.bak")
KEYFILE = os.path.join(HERE, ".access_keys")
KEY_BACKUP = os.path.join(HERE, ".access_keys.bak")
ALLOWFILE = os.path.join(HERE, ".ip_allow")
ALLOW_BACKUP = os.path.join(HERE, ".ip_allow.bak")
CF_LOG = "/tmp/cf-guard.log"
# 隧道日志的可能位置全部列出来。
# 踩过的坑：守护只扫 CF_LOG 这一个文件，而我在手工救服务时
# 把隧道日志写到了 /tmp/cf-q4.log，守护因此报"日志里没有域名"，
# 白白折腾了很久，还误判成 Cloudflare 那边出问题。
# 结论：日志路径不该由多个角色各自决定，统一在这里登记。
CF_LOGS = (CF_LOG, "/tmp/cf-quick.log", "/tmp/cf-quick2.log",
           "/tmp/cf-q3.log", "/tmp/cf-q4.log", "/tmp/cf.log",
           "/tmp/ubcf.log", "/tmp/cf-tunnel.log")

# 内存水位：后端超过 1.2G 就主动重启。
# 为什么不等到 2G（RLIMIT_AS 上限）：到 2G 时 exec 已经在报错，
# 用户会看到"命令莫名失败"，那时再重启是事后补救。这里提前止损。
MEM_LIMIT_KB = 1_200_000
# 响应时间超过这个秒数算僵死。
# 正常是 0.0007s；设 8s（而非 4s）留足余量：偶发的连接排队/冷启动
# 可能产生 5s 级尖峰，4s 阈值会把这种一次性抖动误判成"半僵死"并重启后端。
HTTP_TIMEOUT = 10
STALL_SEC = 8.0
# 连续几轮坏才动手。避免"网络抖一下就重启"这种抖动。
FAIL_TOLERANCE = 2

# 隧道要不要自动重启 —— 默认 **关**。
#
# 为什么默认关：Quick Tunnel 的域名是 Cloudflare 随机分配的，重启必换。
# 也就是说"自动重启隧道"和"让用户手机上的链接保持有效"这两件事
# 在 Quick Tunnel 下是互斥的。之前默认开着，守护在自愈测试里
# kill 掉隧道后自动拉起，用户的旧链接当场失效（Cloudflare 1033）——
# 后端日志一切正常，是最难排查的一类故障。
#
# 所以改成：隧道掉了先只告警不重连，等你确认。
# 真要自动重连，把这里改成 True —— 代价是每次重连换一次网址。
TUNNEL_AUTORESTART = False
# ---- Named Tunnel（固定域名，替代 Quick Tunnel）----
# 2026-10-05 已启用：用户提供了自有域名 3920850391.dpdns.org（DigitalPlat 免费
# 域，NS 已切到 Cloudflare）+ API Token，隧道在 Cloudflare 侧建好、DNS CNAME
# 已指向隧道。地址永久固定为 https://3920850391.dpdns.org ，重启不再变化。
TUNNEL_NAMED_ENABLED = True
TUNNEL_TOKEN_FILE = os.path.join(HERE, ".tunnel", "token")
TUNNEL_CONFIG = os.path.join(HERE, ".tunnel", "config.yml")
TUNNEL_LOG = "/tmp/cf-named.log"
NAMED_MARK = "config.yml"     # 进程命令行里有这个就说明是 Named Tunnel
# Named Tunnel 域名探测缓存（t=上次探测时间, ok=结果），60s 内不重复探测
_NAMED_PROBE = {"t": 0.0, "ok": None}
# ---- 入口注册表（应对 Quick Tunnel 域名随机的问题）----
# 没有自有域名时只能用 Quick Tunnel，域名随机分配、重启即变。
# 把分配过的所有地址都记下来，配合后端存活探测 + 引导页，
# 让"地址失效"不再等于"服务不可用"：打开旧链接会被自动带到活着的入口。
ENTRIES_PATH = os.path.join(HERE, ".entries.json")
_ENTRIES_LOCK = threading.Lock()

_stop = False
# 自己的 pid。用来在扫进程时把自己排除掉。
# 为什么必须按 pid 排除而不是按脚本名：原来写的是
# `if pattern in cl and "guardian.py" not in cl` —— 万一以后脚本改名，
# 或者别人在命令行里带了这串字，这个排除就静默失效，守护会把自己
# 当成"要杀的后端"。按 pid 排除没有这种坑。
SELF_PID = os.getpid()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def glog(msg):
    """守护日志。同时追加到审计日志，这样在手机的安全审计里也能看到。"""
    line = f"[{now()}] {msg}"
    try:
        with open(GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def audit_self(event, detail=""):
    """把自己的动作写进后端审计日志，跟登录失败那些事件混在一条时间线上。"""
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{now()} ip=- GUARD {event}" + (f" {detail}" if detail else "") + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 进程工具

def find_procs(pattern, exclude_self=True):
    """找 pid 列表。

    两个坑都在这里踩过：
      1. 不用 pkill —— 它会连调用方自己的 shell 一起匹配上。
      2. 排除自己必须按 pid，不能按脚本名字符串（见 SELF_PID 处的说明）。
    """
    out = []
    me = SELF_PID
    parent = os.getppid()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            p = int(pid)
            if exclude_self and (p == me or p == parent):
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cl = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except Exception:
                continue
            # 排除 shell 包装进程：它们命令行里带的是整段 eval 文本，
            # 含有 pattern 纯属巧合（我自己的 shell 就中过招）
            if pattern in cl and "/bin/sh" not in cl and "/bin/zsh" not in cl \
               and "codebuddy" not in cl.lower():
                out.append(p)
    except Exception:
        pass
    return out


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            m = re.search(r"VmRSS:\s+(\d+)", f.read())
            return int(m.group(1)) if m else 0
    except Exception:
        return 0


def port_alive():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------- 后端检查

def http_get(path, timeout=HTTP_TIMEOUT):
    t0 = time.time()
    try:
        req = urllib.request.Request(BASE + path, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
            return r.status, time.time() - t0
    except urllib.error.HTTPError as e:
        # 有 HTTP 错误码说明服务在干活，仍然算"活着"
        return e.code, time.time() - t0
    except Exception:
        return 0, time.time() - t0


def check_backend():
    """返回 (健康?, 说明)。健康的标准是真能返回，不是端口在。"""
    pids = find_procs("server.py")
    if not pids:
        return False, "进程不在"
    if not port_alive():
        return False, f"进程在(pid={pids[0]})但端口 {PORT} 没监听"

    code, dt = http_get("/login")
    if code == 0:
        return False, f"HTTP 探活无响应（{dt:.1f}s 超时），判定僵死"
    if code not in (200, 302, 401):
        return False, f"HTTP 返回异常码 {code}"
    if dt > STALL_SEC:
        return False, f"响应 {dt:.1f}s 明显迟缓，判定半僵死"
    return True, f"pid={pids[0]} HTTP{code} {dt*1000:.0f}ms RSS={rss_kb(pids[0])//1024}MB"


def kill_by_port():
    """按监听端口杀进程。

    为什么要这条兜底：重启后端时如果 find_procs 因为任何原因没匹配到
    旧进程（进程名被改、/proc 读取瞬时失败），旧进程会占着 8080 不放，
    新进程起来就 EADDRINUSE 退出 → 守护下一轮又拉 → 又冲突。
    结果是无限重启，而日志里只是一行行 "Address already in use"。
    """
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    killed = []
    for line in out.splitlines():
        if f"127.0.0.1:{PORT}" not in line:
            continue
        for m in re.finditer(r"pid=(\d+)", line):
            p = int(m.group(1))
            if p in (SELF_PID, os.getppid()):
                continue
            try:
                os.kill(p, signal.SIGKILL)
                killed.append(p)
            except Exception:
                pass
    return killed


def stop_backend():
    """彻底停掉后端：先 SIGTERM 給它收尾机会，再 SIGKILL，最后按端口兜底。"""
    for p in find_procs("server.py"):
        try:
            os.kill(p, signal.SIGTERM)
        except Exception:
            pass
    time.sleep(2)
    for p in find_procs("server.py"):
        try:
            os.kill(p, signal.SIGKILL)
        except Exception:
            pass
    time.sleep(1)
    extra = kill_by_port()
    if extra:
        glog(f"按端口兜底清掉残留进程 {extra}")
    return extra


def start_backend():
    glog("拉起后端…")
    audit_self("RESTART", "后端不自愈，手工拉起")
    key = read_key()
    env = dict(os.environ)
    if key:
        env["LINUXSIM_KEY"] = key
    subprocess.Popen(
        ["python3.11", os.path.join(HERE, "server.py")],
        cwd=HERE, env=env,
        stdout=open(SRV_LOG, "a", buffering=1),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(20):
        time.sleep(1)
        code, _ = http_get("/login", timeout=3)
        if code in (200, 302, 401):
            glog(f"后端已恢复，HTTP{code}")
            return True
    glog("后端启动失败，详见 " + SRV_LOG)
    return False


# ---------------------------------------------------------------- 隧道检查

def entries_load():
    with _ENTRIES_LOCK:
        try:
            with open(ENTRIES_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, list):
                return d
        except Exception:
            pass
        return []


def entries_save(lst):
    with _ENTRIES_LOCK:
        try:
            tmp = ENTRIES_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(lst, f, ensure_ascii=False, indent=1)
            os.replace(tmp, ENTRIES_PATH)     # 原子替换，避免守护读到半个文件
            return True
        except Exception as e:
            glog(f"入口注册表写入失败: {e}")
            return False


def entry_register(url, kind):
    """登记一个入口。已存在就只更新时间戳。"""
    if not url:
        return False
    url = url.rstrip("/")
    lst = entries_load()
    for e in lst:
        if e.get("url") == url:
            e["last_seen"] = now()
            e["kind"] = kind
            return False
    lst.append({"url": url, "kind": kind, "first_seen": now(), "last_seen": now()})
    # 只留最近 20 个，文件不会无限长
    lst = lst[-20:]
    entries_save(lst)
    glog(f"登记入口 [{kind}] {url}")
    audit_self("ENTRY_ADD", f"{kind} {url}")
    return True


def entry_verify_all():
    """实测每个登记过的入口还通不通，把结果写回。

    这一步是整个方案的关键：不能靠"文件里记着"判断地址有效，
    必须真的发一次请求。早先踩过的坑就是日志里留着旧域名、
    守护据此认为一切正常，而那个域名 DNS 早已不解析。
    """
    lst = entries_load()
    if not lst:
        return []
    changed = False
    for e in lst:
        alive = tunnel_url_alive(e["url"])
        old = e.get("alive")
        e["alive"] = bool(alive)
        e["checked"] = now()
        if old != e["alive"]:
            changed = True
            if alive:
                glog(f"入口恢复: {e['url']}")
            else:
                glog(f"入口失效: {e['url']}")
                audit_self("ENTRY_DOWN", e["url"])
    if changed:
        entries_save(lst)
    return lst


def entry_best():
    """当前该推荐给用户的地址。

    优先级：named（永久域名）> quick（随机域名），
    同类里取最近见到的。
    """
    lst = [e for e in entries_load() if e.get("alive")]
    if not lst:
        return None
    for kind in ("named", "quick"):
        same = [e for e in lst if e.get("kind") == kind]
        if same:
            return max(same, key=lambda x: x.get("last_seen", "")).get("url")
    return lst[-1].get("url")


def _named_hostname():
    """从配置文件里读出 Named Tunnel 绑定的域名。

    读文件而不是靠探测猜：配置是我们唯一确定的信息源。
    之前靠猜域名（先猜 linuxsim.trycloudflare.com）就错过一次，
    结果 .public_url 里存着一个根本不存在的地址。
    """
    try:
        with open(TUNNEL_CONFIG, "r", encoding="utf-8") as f:
            m = re.search(r"hostname:\s*(\S+)", f.read())
        return m.group(1) if m else None
    except Exception:
        return None


def tunnel_pids(named=False):
    """找 cloudflared 进程。

    named=True 只找 Named Tunnel（带 config.yml 的那个），
    named=False 只找 Quick Tunnel（--url 的那个）。
    分开是因为两种隧道用途完全不同：Quick Tunnel 网址随机、一重启就换，
    挂了不该自动重连（换了等于把你手机上的链接作废）；
    Named Tunnel 网址固定，重连没有任何副作用，必须自动恢复。
    """
    out = []
    for pid in find_procs("cloudflared tunnel"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cl = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        is_named = NAMED_MARK in cl
        if is_named == named:
            out.append(pid)
    return out


def start_named_tunnel():
    """拉起 Named Tunnel。

    和 Quick Tunnel 不同：这个可以放心重连。域名是固定的，
    重启后你手机上存的地址照样能用。
    """
    # 去重：已有 Named Tunnel 实例在跑，绝不再拉第二个（否则会累积成多实例，
    # 请求被轮询到正在重启的实例，造成间歇性 502）。
    if tunnel_pids(named=True):
        return True
    if not os.path.exists(TUNNEL_TOKEN_FILE):
        glog("Named Tunnel 缺 token 文件，无法拉起")
        return False
    try:
        with open(TUNNEL_TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
    except Exception as e:
        glog(f"读 token 失败: {e}")
        return False
    glog("拉起 Named Tunnel（固定域名，重连不会换网址）…")
    audit_self("NAMED_TUNNEL_START", "cloudflared named 不在，自动拉起")
    # 2026-10-18 起：改用 credentials 文件 + 本地 config.yml（本地 ingress）。
    # 之前 --token 是远程管理模式，会忽略本地 ingress，导致
    # system.3920850391.dpdns.org（管理台）等本地新加子域 404。
    try:
        subprocess.Popen(
            ["/tmp/cloudflared", "tunnel", "--no-autoupdate", "--protocol", "http2",
             "--config", TUNNEL_CONFIG, "run",
             "7ed54865-e16c-44fc-a03a-1708f9ae61d6"],
            stdout=open(TUNNEL_LOG, "a", buffering=1),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        glog(f"Named Tunnel 拉起失败: {e}")
        return False
    for _ in range(25):
        time.sleep(1)
        if tunnel_pids(named=True):
            glog("Named Tunnel 已恢复")
            return True
    glog("Named Tunnel 启动超时，见 " + TUNNEL_LOG)
    return False


def named_tunnel_healthy():
    """Named Tunnel 是否连上了 Cloudflare。

    判据是日志里的连接注册记录，而且必须是"最近"产生的 ——
    进程在但连接全断了的情况（网络切换、Cloudflare 侧踢掉）很常见，
    光看进程在会把这种故障放过去。
    """
    if not tunnel_pids(named=True):
        return False, "Named Tunnel 进程不在"
    # 首选真实探测：能通过固定域名打到本机后端 = 一定健康。
    # 不能只靠日志时间戳：稳定运行的隧道不再产生新的注册日志，
    # 只看时间戳会把正常隧道误判"过期"，每 10 分钟无谓重连一次。
    # 探测结果缓存 60s，避免每轮（10s）都发一次 HTTPS 请求。
    host = _named_hostname()
    if host:
        now_ts = time.time()
        if now_ts - _NAMED_PROBE["t"] > 60:
            _NAMED_PROBE["ok"] = tunnel_url_alive("https://" + host)
            _NAMED_PROBE["t"] = now_ts
        if _NAMED_PROBE["ok"]:
            return True, f"域名 {host} 实测可达"
    try:
        with open(TUNNEL_LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-80:]
    except Exception:
        return False, "读不到 Named Tunnel 日志"
    conns = [x for x in lines if "Registered tunnel connection" in x]
    if len(conns) < 2:
        return False, f"只有 {len(conns)} 条连接注册（正常应有 4 条）"
    # 兜底判据：连接注册时间放宽到 30 分钟内
    newest = conns[-1]
    try:
        ts = newest.split(" ")[0].replace("T", " ").split(".")[0]
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - t).total_seconds()
        if age > 1800:
            return False, f"连接记录已过期 {int(age)}s（可能已掉线）"
    except Exception:
        pass
    return True, f"{len(conns)} 条连接在线"


def start_tunnel():
    """拉起 Quick Tunnel，并把新 URL 记下来。

    重点：Quick Tunnel 的域名是随机分配的，重启就换。
    如果不把 URL 落盘，用户手机里存的链接会在某次重启后静默失效 ——
    而后端日志一切正常，是最难排查的一类故障。
    """
    glog("拉起隧道…")
    audit_self("TUNNEL_RESTART", "cloudflared 不在，自动拉起")
    try:
        subprocess.Popen(
            ["/tmp/cloudflared", "tunnel", "--url", f"http://localhost:{PORT}",
             "--no-autoupdate"],
            stdout=open(CF_LOG, "a", buffering=1),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        glog(f"隧道拉起失败: {e}")
        return False

    # 给它 20 秒分配域名
    for _ in range(20):
        time.sleep(1)
        u = read_tunnel_url()
        if u:
            # 关键：把"重启前落盘的旧地址"作为 old 传进去。旧代码传 None，
            # 导致隧道重启拿到新域名后**不生成变更横幅** —— 你手机里的
            # 旧链接其实已经死了，首页却什么提示都没有。旧域名此刻还在
            # .public_url 里（隧道刚重启、尚未覆盖），正好当作 old。
            save_tunnel_url(u, load_tunnel_url())
            return True
    glog("隧道起来了但没解析到 URL，检查 " + CF_LOG)
    return False


def tunnel_log_path():
    """找到当前活跃 cloudflared 进程真实在写的日志文件。

    这一步不能省。之前写死读 /tmp/cf.log，结果那个文件是更早一次隧道留下的
    旧日志 —— 守护从里面扒出一个**已经失效的域名**，写进 .public_url，
    你手机里存的真链接反而被当成"变了"。公网实测：真链接 200，
    日志里的旧域名 000（DNS 都不解析）。正确做法是顺着进程 fd 找。
    """
    for pid in tunnel_pids():
        try:
            for fd in ("1", "2"):
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
                # 必须是真实文件，不能是 socket/pipe ——
                # 多进程时可能抓到别人的管道，readlink 得到 "socket:[xxx]"
                # 根本不是日志文件
                if link and link.startswith("/") and os.path.isfile(link):
                    return link
        except Exception:
            continue
    return CF_LOG


def read_tunnel_url():
    """从 cloudflared 日志里扒出分配到的域名。

    多个候选时必须**逐个实测**，取第一个真能通的。
    这里不信任"日志里最后一条就是当前" —— 实测过两次教训：
      · 日志文件可能是上一次隧道的残留（换过日志文件名）
      · 同一文件里可能有多次隧道的记录，早失效的域名也在里面
    """
    paths = []
    p = tunnel_log_path()
    if p:
        paths.append(p)
    # 兜底把所有可能的日志都扫一遍。用 CF_LOGS 统一登记的清单，
    # 别再在这里硬编码几个路径 —— 漏一个就"日志里没有域名"。
    for extra in CF_LOGS:
        if extra not in paths and os.path.isfile(extra):
            paths.append(extra)
    # 最后兜底：/tmp 下任何含 trycloudflare 的日志文件都扫一遍。
    # 沙箱里 /tmp 会被各种临时文件塞满，宁可多读几个也别漏掉目标。
    try:
        for name in os.listdir("/tmp"):
            if not (name.startswith("cf") or "cloudflare" in name
                    or "tunnel" in name):
                continue
            full = os.path.join("/tmp", name)
            if full not in paths and os.path.isfile(full):
                try:
                    if os.path.getsize(full) < 4 * 1024 * 1024:
                        paths.append(full)
                except Exception:
                    pass
    except Exception:
        pass

    cands = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for u in re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read()):
                    if u not in cands:
                        cands.append(u)
        except Exception:
            continue
    if not cands:
        return None
    # 最近的优先，但最终以实测结果为准
    for u in reversed(cands[-8:]):
        if tunnel_url_alive(u):
            return u
    return None


def tunnel_url_alive(u, timeout=5):
    """这个域名现在还通不通。

    判断标准必须是"**打到了我们自己的后端**"，而不是"有 HTTP 响应"。
    这里踩过一个很隐蔽的坑：Cloudflare 对已断开的隧道返回 530
    （而且是带完整 HTML 错误页的正常 HTTP 响应，不是连接失败），
    所以"有响应码就算活"的写法会把死域名判成活的 ——
    实测死域名 called-objects 返回 530，被误判 alive=True，
    于是守护一直把你往一个打不开的地址上引导。

    正确判据：响应体里含有我们后端独有的标记
    （口令页标题）。这样 Cloudflare 的 5xx 错误页一律判死。
    """
    try:
        req = urllib.request.Request(u.rstrip("/") + "/login.html", method="GET")
        # 不带 UA 会被 Cloudflare 浏览器完整性检查挡成 403/1010；
        # 加个正常浏览器 UA，才能真的打到我们后端（而非 Cloudflare 拦页面）
        req.add_header("User-Agent",
                       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2048).decode("utf-8", "replace")
        # 打到了我们的登录页才算数
        return ("口令" in body) or ("linux-sim" in body.lower()) \
            or ("访问口令" in body) or ("Ubuntu 24.04" in body)
    except urllib.error.HTTPError as e:
        # 401 是本站点登录页的 CSRF 守卫（GET 未带 Origin 头），
        # 说明请求确实打到了我们自己的后端 —— 活。
        # 只有 Cloudflare 对死隧道返回的 5xx（530 等）才算死，
        # 那条路走下面的 return False，不会误报。
        if e.code == 401:
            return True
        return False
    except Exception:
        return False


def load_tunnel_url():
    try:
        with open(URL_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def save_tunnel_url(new, old):
    # 先校正"地址变更"横幅。横幅的作用是告诉用户"你手机里存的旧链接
    # 失效了，改用这个新地址"。一旦横幅里写的"新地址"跟当前真实活跃
    # 域名对不上，这条横幅就是在把用户往一个打不开的死链上引 ——
    # 实测事故：`.public_url` 已是 rural-derived，横幅却还挂着更早一次
    # 重启写下的 kentucky（HTTP 000）。所以宁可不显示，也不显示错的。
    _reconcile_url_notice(new)
    if new == old:
        return
    try:
        if old and os.path.exists(URL_FILE):
            shutil.copy2(URL_FILE, URL_BACKUP)
        with open(URL_FILE, "w", encoding="utf-8") as f:
            f.write(new + "\n")
        os.chmod(URL_FILE, 0o600)
    except Exception as e:
        glog(f"URL 落盘失败: {e}")
        return
    if old and old != new:
        # 这种情况必须高调报警：用户手机里存的旧链接已经失效了。
        # 写进 .public_url 只是一半 —— 用户看不到文件。真正管用的是
        # 把它塞进后端会话，这样他一打开模拟器，首页就能直接看到新地址。
        glog(f"!! 隧道 URL 变了：{old} -> {new}  （你手机里存的旧链接已失效）")
        audit_self("URL_CHANGED", f"{old} -> {new} 手机旧链接失效")
        _publish_url_notice(new, old)
    else:
        glog(f"隧道 URL: {new}")
        audit_self("URL_OK", f"当前 {new}")


def _publish_url_notice(new, old):
    """把"网址变了"直接推给后端，让用户一进来就看见。

    做法是写一个 notice 文件，后端每次渲染首页都读它并显示。
    比只写日志有用得多 —— 日志要主动去翻，首页提示是推到你眼前的。
    """
    try:
        with open(os.path.join(HERE, ".url_notice.json"), "w", encoding="utf-8") as f:
            json.dump({"new": new, "old": old, "at": now()}, f, ensure_ascii=False)
    except Exception as e:
        glog(f"网址变更通知写入失败: {e}")


def _reconcile_url_notice(live):
    """横幅里的"新地址"与真实活跃域名不一致时，直接撤下这条横幅。

    只清不写：真正的"地址变更"通告由 save_tunnel_url 在检测到
    new != old 时调 _publish_url_notice 生成。这里只负责把**已经对不上**
    的过期横幅清掉，避免它继续误导用户。
    """
    if not live:
        return
    p = os.path.join(HERE, ".url_notice.json")
    try:
        if not os.path.exists(p):
            return
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        shown = (d.get("new") or "").rstrip("/")
        if shown and shown != live.rstrip("/"):
            glog("撤下过期地址变更横幅：横幅写的 %s 与真实活跃 %s 不符"
                 % (shown, live.rstrip("/")))
            os.remove(p)
    except Exception:
        pass


def clear_url_notice():
    """通知只保留 24 小时，之后自动撤掉 —— 否则你会一直看到一个过期警告。"""
    p = os.path.join(HERE, ".url_notice.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            t = datetime.strptime(d.get("at", ""), "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - t).total_seconds() > 86400:
                os.remove(p)
    except Exception:
        pass


def check_tunnel():
    """返回 (健康?, 说明)。顺便盯住 URL 变化。"""
    pids = tunnel_pids()
    if not pids:
        return False, "cloudflared 进程不在"
    # 后端自己在重启时，隧道打到本地也是 530/连不上，
    # read_tunnel_url() 会因为"实测全部不通"而返回 None。
    # 这时候报"日志里没有域名"是误导 —— 日志里明明有。
    # 先确认后端活不活，把原因说准确，日志才有排查价值。
    if http_get("/login", timeout=3)[0] == 0:
        return False, "后端正在重启，隧道暂时打不通（属正常，等下一轮）"
    cur = read_tunnel_url()
    if not cur:
        return False, "日志里的域名实测均不通（后端已活着，不是后端问题）"
    save_tunnel_url(cur, load_tunnel_url())
    return True, f"pid={pids[0]} {cur}"


# ---------------------------------------------------------------- 资源检查

def check_files():
    """日志/会话体积。磁盘被撑爆时后端连日志都写不进去。"""
    msgs = []
    for path, cap_mb, action in (
        (SRV_LOG, 64, "truncate"),
        (GUARD_LOG, 8, "truncate"),
        (CF_LOG, 16, "truncate"),
        (os.path.join(HERE, ".sessions.db"), 32, None),
    ):
        try:
            if not os.path.exists(path):
                continue
            mb = os.path.getsize(path) / 1048576
            if mb > cap_mb and action == "truncate":
                # 保留尾部，前面丢掉。不能直接删 —— 事后要查"崩之前发生了什么"
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-3000:]
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(tail)
                glog(f"{os.path.basename(path)} {mb:.0f}MB 超限，已裁剪到尾部")
                audit_self("TRIM", f"{os.path.basename(path)} {mb:.0f}MB→裁剪")
        except Exception as e:
            msgs.append(f"裁剪 {os.path.basename(path)} 失败: {e}")
    return (not msgs), "; ".join(msgs) or "正常"


def read_key():
    try:
        with open(KEYFILE, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    return ln
    except Exception:
        pass
    return None


def check_keys():
    """密钥文件必须存在、权限 600、至少有一条非注释行。

    为什么这么要紧：密钥文件要是丢了，后端启动时会 os.urandom(12).hex()
    随机生成一个新的 —— 你会发现自己的口令突然失效，而且完全不知道原因。
    """
    if not os.path.exists(KEYFILE):
        if os.path.exists(KEY_BACKUP):
            shutil.copy2(KEY_BACKUP, KEYFILE)
            os.chmod(KEYFILE, 0o600)
            glog("!! 密钥文件丢失，已从备份恢复")
            audit_self("KEY_RESTORE", "从 .access_keys.bak 恢复")
            return True, "已从备份恢复"
        glog("!! 密钥文件丢失且无备份！后端会随机生成新口令，你将无法登录")
        audit_self("KEY_LOST", "无备份，无法自动恢复")
        return False, "密钥丢失且无备份"

    k = read_key()
    if not k:
        return False, "密钥文件里没有有效密钥行"

    mode = os.stat(KEYFILE).st_mode & 0o777
    if mode != 0o600:
        os.chmod(KEYFILE, 0o600)
        glog(f"密钥文件权限 {oct(mode)} 太宽，已收紧到 600")
        audit_self("KEY_CHMOD", f"{oct(mode)}→0o600")
    # 备份一份，供上面那条恢复路径用
    try:
        shutil.copy2(KEYFILE, KEY_BACKUP)
        os.chmod(KEY_BACKUP, 0o600)
    except Exception:
        pass
    return True, f"主密钥 {k[:2]}***（{len(k)} 位）"


def check_allow():
    """白名单。

    空文件是合法的（表示"还没绑定任何人"），但**曾经有内容现在突然空了**
    是个危险信号 —— 可能是被误操作清掉，那意味着主密钥会对全网开放。
    有备份就还原。
    """
    if not os.path.exists(ALLOWFILE):
        if os.path.exists(ALLOW_BACKUP):
            shutil.copy2(ALLOW_BACKUP, ALLOWFILE)
            os.chmod(ALLOWFILE, 0o600)
            glog("!! 白名单文件丢失，已从备份恢复")
            audit_self("ALLOW_RESTORE", "从 .ip_allow.bak 恢复")
            return True, "已从备份恢复"
        return True, "未启用（首次绑定前，全网可试主密钥）"

    try:
        with open(ALLOWFILE, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except Exception as e:
        return False, f"读取失败: {e}"

    ips = [x.strip() for x in body.splitlines()
           if x.strip() and not x.strip().startswith("#")]

    if not ips:
        if os.path.exists(ALLOW_BACKUP):
            with open(ALLOW_BACKUP, "r", encoding="utf-8", errors="replace") as f:
                b = [x.strip() for x in f.read().splitlines()
                     if x.strip() and not x.strip().startswith("#")]
            if b:
                shutil.copy2(ALLOW_BACKUP, ALLOWFILE)
                glog(f"!! 白名单被清空但有备份（{len(b)} 条），已还原")
                audit_self("ALLOW_RESTORE", f"被清空，还原 {len(b)} 条")
                return True, f"已还原 {len(b)} 条"
        return True, "空（未绑定，主密钥暂不限 IP）"

    try:
        shutil.copy2(ALLOWFILE, ALLOW_BACKUP)
        os.chmod(ALLOW_BACKUP, 0o600)
    except Exception:
        pass
    return True, f"{len(ips)} 条白名单"


# ---------------------------------------------------------------- 主循环

def on_exit(signum, frame):
    global _stop
    _stop = True


def main():
    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)

    glog("=" * 60)
    glog("24 小时自愈守护启动，巡检间隔 %ds" % INTERVAL)
    # 启动时以"真实活跃域名"为准（旧代码优先取落盘值，会把过期域名当成当前）。
    old = load_tunnel_url()
    u = read_tunnel_url() or old
    if u:
        # 传 old：若守护停机期间隧道换过域名，这里会补发一次变更横幅，
        # 让用户知道手机里存的旧链接已失效。
        save_tunnel_url(u, old)
    audit_self("GUARD_START", f"守护上线 间隔{INTERVAL}s")

    fails = {"backend": 0, "tunnel": 0, "files": 0}
    stats = {"restart": 0, "url_change": 0, "round": 0}
    last_report = time.time()
    last_entries = 0.0

    while not _stop:
        stats["round"] += 1
        acted = []

        # 1) 后端
        ok, why = check_backend()
        if not ok:
            fails["backend"] += 1
            glog(f"后端异常（第 {fails['backend']} 轮）: {why}")
            if fails["backend"] >= FAIL_TOLERANCE:
                stop_backend()
                start_backend()
                stats["restart"] += 1
                acted.append("后端重启")
                fails["backend"] = 0
        else:
            if fails["backend"]:
                glog(f"后端自愈（无需手工干预）")
            fails["backend"] = 0
            # 2) 内存水位：健康但太胖也主动重启
            pids = find_procs("server.py")
            if pids:
                r = rss_kb(pids[0])
                if r > MEM_LIMIT_KB:
                    glog(f"后端内存 {r//1024}MB 超水位 {MEM_LIMIT_KB//1024}MB，主动重启止损")
                    audit_self("MEM_HIGH", f"{r//1024}MB 超阈值，重启")
                    stop_backend()
                    start_backend()
                    stats["restart"] += 1
                    acted.append(f"内存重启({r//1024}MB)")

        # 3) 隧道 —— Named Tunnel（需自有域名，否则是死路）
        #    TUNNEL_NAMED_ENABLED=False 时一律走 Quick Tunnel 分支。
        #    之前靠 `os.path.exists(TUNNEL_TOKEN_FILE)` 判断，可 token 文件
        #    一直在，于是即使域名根本不通，也会一轮轮"异常→重连"刷日志，
        #    还会把 Quick Tunnel 挤掉（最后两个隧道都没了，公网全断）。
        if TUNNEL_NAMED_ENABLED and os.path.exists(TUNNEL_TOKEN_FILE):
            nok, nwhy = named_tunnel_healthy()
            if not nok:
                fails["tunnel"] += 1
                glog(f"Named Tunnel 异常（第 {fails['tunnel']} 轮）: {nwhy}")
                if fails["tunnel"] >= FAIL_TOLERANCE:
                    # 固定域名，重连没有副作用 —— 放心恢复
                    start_named_tunnel()
                    acted.append("Named Tunnel 重连")
                    fails["tunnel"] = 0
            else:
                if fails["tunnel"]:
                    glog("Named Tunnel 已自愈")
                fails["tunnel"] = 0
        else:
            tok, twhy = check_tunnel()
            if not tok:
                fails["tunnel"] += 1
                # ---- 关键区分：进程没了 vs 进程还在但连接断了 ----
                # 沙箱每次休眠唤醒，所有进程都被清空。之前这种情况下
                # 守护不重连（怕换域名），结果就是"你手机里链接作废"
                # 之外还叠加"服务彻底不可用"，你只能来叫我。
                #
                # 现在改成：
                #   进程完全不存在 → 自动拉起。理由：这时候服务已经 100%
                #     不可用了，不拉起没有任何损失；而拉起来你至少还有个
                #     能打开的地址。地址变了你用 /where 查。
                #   进程还在，只是连接断了 → 仍然不自动重连。理由：这时
                #     服务是"能用的"，重连纯属主动破坏你手里的链接。
                pids_now = tunnel_pids()
                if not pids_now:
                    start_tunnel()
                    acted.append("隧道进程消失，已重新拉起")
                    glog("隧道进程不存在（沙箱重启或被清空），已自动拉起。"
                         "地址可能已变，用 /where 查当前地址")
                    audit_self("TUNNEL_RESPAWN",
                               "隧道进程消失，已自动拉起（此前是等服务彻底不可用）")
                    fails["tunnel"] = 0
                elif fails["tunnel"] >= FAIL_TOLERANCE and TUNNEL_AUTORESTART:
                    for p in tunnel_pids():
                        try:
                            os.kill(p, signal.SIGTERM)
                        except Exception:
                            pass
                    time.sleep(2)
                    start_tunnel()
                    acted.append("隧道重启")
                    fails["tunnel"] = 0
                elif not TUNNEL_AUTORESTART and fails["tunnel"] >= FAIL_TOLERANCE:
                    # 进程还在，只是连接异常：不重连，保护你手机里的链接
                    glog("  隧道连接异常但进程还在，按保守策略不自动重连"
                         "（重连会换域名）。当前服务仍可用，若持续异常告诉我。")
                    audit_self("TUNNEL_DOWN",
                               "Quick Tunnel 挂掉，为保护用户链接不自动重连")
                    fails["tunnel"] = 0     # 复位，否则每轮都刷这条
            else:
                if fails["tunnel"]:
                    glog("隧道已自愈")
                fails["tunnel"] = 0

        # 4) 文件体积
        fok, fwhy = check_files()
        if not fok:
            glog("文件维护: " + fwhy)

        # 5) 密钥
        kok, kwhy = check_keys()
        if not kok:
            glog(f"!! 密钥异常: {kwhy}")
            audit_self("KEY_BAD", kwhy)

        # 6) 白名单
        aok, awhy = check_allow()
        if not aok:
            glog(f"!! 白名单异常: {awhy}")

        # 7) 过期通知清理
        clear_url_notice()

        # 8) 入口注册表
        # 关键：入口必须每轮都从"活跃进程的日志"重新实测，不能只读
        # .public_url 文件。之前只在 5 分钟一轮里刷新文件，结果 Quick Tunnel
        # 换了域名之后，守护整整 2 分钟还拿着旧地址向你推荐 ——
        # 而那 2 分钟里你打开旧链接就是 530。
        # 现在每轮都实测（有 3s 缓存，代价可忽略），并把变化立刻写盘。
        if time.time() - last_entries > 60:
            last_entries = time.time()
            live = read_tunnel_url()          # 活跃 Quick Tunnel 的真实域名
            if live:
                entry_register(live, "quick")
                save_tunnel_url(live, load_tunnel_url())
            if tunnel_pids(named=True):
                nh = _named_hostname()
                if nh:
                    entry_register("https://" + nh, "named")
            entry_verify_all()
            best = entry_best()
            if best:
                save_tunnel_url(best, load_tunnel_url())
            # 当前没有任何存活入口 = 隧道全断了，立刻高调告警
            if not best:
                glog("!! 所有入口均已失效 —— 隧道可能全部断开，请检查 cloudflared")
                audit_self("ALL_ENTRIES_DOWN", "没有任何可用的公网入口")

        if acted:
            glog("本轮动作: " + ", ".join(acted))

        # 每小时记一次心跳，方便你直接查"它还活着吗"
        if time.time() - last_report >= 3600:
            pub = load_tunnel_url() or "-"
            alive_n = sum(1 for e in entries_load() if e.get("alive"))
            glog(f"心跳 巡检{stats['round']}轮 重启{stats['restart']}次 "
                 f"入口{alive_n}个可用 出口 {pub}")
            audit_self("HEARTBEAT",
                       f"rounds={stats['round']} restarts={stats['restart']} "
                       f"entries={alive_n} url={pub}")
            # 没有固定域名这件事要说清楚：Quick Tunnel 的域名随机分配，
            # 进程一重启就换，用户手机里存的链接会失效。
            # 根治只能靠自有域名（Named Tunnel），这条路走不通时要讲明白，
            # 否则用户会以为是 bug。
            if not tunnel_pids(named=True) and os.path.exists(TUNNEL_TOKEN_FILE):
                glog("提示：当前只有 Quick Tunnel（域名随机、重启即变）。"
                     "换自有域名后可切到 Named Tunnel，域名永久固定。")
            last_report = time.time()

        for _ in range(INTERVAL):
            if _stop:
                break
            time.sleep(1)

    glog("守护退出")
    audit_self("GUARD_STOP", "收到停止信号")


if __name__ == "__main__":
    main()

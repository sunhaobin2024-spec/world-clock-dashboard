#!/usr/bin/env python3
# cloudsystem 后端：静态服务 + 真实 bash 接入 + 网页代理
import http.server, socketserver, json, subprocess, os, base64, shlex, threading
import hashlib
import re, socket, ipaddress, urllib.request, urllib.parse, urllib.error
import time, hmac, secrets, fcntl
import random
from urllib.parse import urlparse, parse_qs, unquote

# ---- vm64 进程状态缓存 ----
# 旧实现每次状态查询都 fork `pgrep`（实测单次 ~13ms，是请求路径上唯一的慢点）。
# 改为：后台线程每 3s 直接读 /proc 刷新缓存（无 subprocess、无 fork），
# 请求路径（/api/vm64/status）纯字典读取，开销从 ~13ms 降到亚毫秒。
# ---------------- vm64 状态：后台 /proc 扫描 + 多信号交叉校验缓存 ----------------
# 设计目标：请求路径零开销（直读缓存，不拖慢访问），后台线程更密集地扫描 /proc
# 并交叉校验"真实监听端口"，既提高精度，又消除"命令行子串匹配"导致的误报。
_VM64_CACHE = {"running": False, "novnc": False, "pid": None, "vnc_port": None,
               "ts": 0.0, "scans": 0, "stale": False}
_VM64_CACHE_LOCK = threading.Lock()
_VM64_CACHE_INTERVAL = float(os.environ.get("VM64_SCAN_INTERVAL", "1.0"))  # 比原来 3s 更密

def _listening_tcp_ports():
    """解析 /proc/net/tcp 与 /proc/net/tcp6，返回处于 LISTEN 的本地端口集合（零 fork）。"""
    ports = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f, "r"):
                p = line.split()
                if len(p) < 4 or p[3] != "0A":   # 0A = TCP_LISTEN
                    continue
                local = p[1]                       # 形如 0100007F:1770
                try:
                    ports.add(int(local.split(":")[1], 16))
                except (ValueError, IndexError):
                    continue
        except OSError:
            continue
    return ports

def _vm64_scan_procs():
    """
    直接读 /proc 扫描 qemu(vm64) 与 websockify(8081)，零 subprocess fork。
    多信号交叉校验（从根上消除"命令行里出现关键字"的误报）：
      · qemu(vm64) 在跑 = argv[0] 以 qemu-system-x86_64 结尾 且 存在相邻参数 "-name" "vm64"
                          且 5902(VNC) 真实处于 LISTEN
      · novnc 在线       = argv[0] 以 websockify 结尾 且 8081 真实处于 LISTEN
    仅做子串匹配会把"命令行里出现这些字的监控/调试进程"误判为在跑，故必须叠加端口校验。
    """
    qemu_pid = None
    novnc = False
    vnc_port = None
    try:
        listening = _listening_tcp_ports()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                argv = [a for a in open(os.path.join("/proc", pid, "cmdline"), "rb").read().split(b"\x00") if a]
            except OSError:
                continue
            if not argv:
                continue
            cmd0 = argv[0].decode("utf-8", "replace")
            toks = [a.decode("utf-8", "replace") for a in argv]
            if cmd0.endswith("qemu-system-x86_64"):
                if "-name" in toks:
                    i = toks.index("-name")
                    if i + 1 < len(toks) and toks[i + 1] == "vm64" and 5902 in listening:
                        qemu_pid = int(pid)
                        vnc_port = 5902
            elif any("websockify" in t for t in toks) and 8081 in listening:
                novnc = True
    except OSError:
        pass
    return qemu_pid, novnc, vnc_port

def _vm64_cache_thread():
    while True:
        try:
            pid, novnc, vnc_port = _vm64_scan_procs()
            with _VM64_CACHE_LOCK:
                _VM64_CACHE["pid"] = pid
                _VM64_CACHE["novnc"] = novnc
                _VM64_CACHE["vnc_port"] = vnc_port
                _VM64_CACHE["running"] = pid is not None
                _VM64_CACHE["ts"] = time.time()
                _VM64_CACHE["scans"] += 1
                _VM64_CACHE["stale"] = False
        except Exception:
            pass
        time.sleep(_VM64_CACHE_INTERVAL)

def _vm64_cache_read(fresh=False):
    """请求路径读缓存；写操作（start/stop）传 fresh=True 立即重扫保证权威。
    两类兜底：① 缓存 pid 进程已退出 → 强制重扫；② 缓存时间戳过旧（后台线程疑似停滞）
    → 强制重扫，避免返回陈旧结果。"""
    with _VM64_CACHE_LOCK:
        running, novnc, pid, ts, scans = (_VM64_CACHE["running"], _VM64_CACHE["novnc"],
                                         _VM64_CACHE["pid"], _VM64_CACHE["ts"], _VM64_CACHE["scans"])
    if pid is not None and not os.path.exists(os.path.join("/proc", str(pid))):
        fresh = True
    if not fresh and (time.time() - ts) > 2 * _VM64_CACHE_INTERVAL:
        fresh = True
    if fresh:
        pid, novnc, vnc_port = _vm64_scan_procs()
        with _VM64_CACHE_LOCK:
            _VM64_CACHE["pid"] = pid
            _VM64_CACHE["novnc"] = novnc
            _VM64_CACHE["vnc_port"] = vnc_port
            _VM64_CACHE["running"] = pid is not None
            _VM64_CACHE["ts"] = time.time()
            running, novnc = _VM64_CACHE["running"], _VM64_CACHE["novnc"]
    return running, novnc, pid

PORT = 8080
USER_DIR = "/workspace/linux-sim/userfiles"
os.makedirs(USER_DIR, exist_ok=True)

# 应用商店：真实下载到后端 + 单用户隔离（反馈 FB20261017220811AAB3）
STORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store")
os.makedirs(STORE_DIR, exist_ok=True)
STORE_CATALOG = os.path.join(STORE_DIR, "catalog.json")

# ---------------- 安全层 ----------------
# 这个后端有 /api/exec（真实 shell）和 /api/proxy（可读任意公网页面），
# 挂在公网上必须鉴权。否则任何拿到 URL 的人都能在你的沙箱里执行命令。
#
# 认证用标准 Authorization: Bearer <token>：
#   - token 走请求头，不进 URL，所以不会出现在浏览器历史 / Referer / 隧道日志里
#   - 前端存 sessionStorage，每次 fetch 自动带
#   - 首次访问弹口令输入页（见 /login），对了才拿到 token 进桌面
#   - 也接受 HttpOnly Cookie（首屏导航时用 ?key= 换），便于直接贴链接访问
# ---- 访问密钥 ----
# 支持多个密钥并存：主密钥（自己长期用）+ 备用密钥（对外测试/分享用）。
# 分成两个是为了出事时能只作废一个，另一个照常使用；
# 而且审计日志能记清是哪个密钥进来的 —— 只用单个密钥时没法区分。
# 文件格式：每行一个，第一个是主密钥，# 开头为注释。权限 600。
KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".access_keys")


def _load_keys():
    out = []
    try:
        with open(KEYFILE, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.append(ln)
    except Exception:
        pass
    env = os.environ.get("LINUXSIM_KEY", "").strip()
    if env and env not in out:
        out.insert(0, env)
    if not out:
        out.append(os.urandom(12).hex())
    return out


KEYS = _load_keys()
ACCESS_KEY = KEYS[0]                 # 主密钥，保持旧代码兼容
ALT_KEYS = set(KEYS[1:]) if len(KEYS) > 1 else set()


# ---- 主密钥的 IP 白名单 ----
# 主密钥只允许本人的 IP 使用。但用户走的是移动网络（中国广电），出口 IP
# 会随基站切换/重连变化，死绑一个 IP 等于迟早把自己锁在门外。
# 所以做成：默认锁白名单里的 IP；IP 变了之后用备用密钥进来执行
# /api/trust 加一条即可（应急解锁），永远不会把自己彻底锁死。
IPLIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ip_allow")
_ip_lock = threading.Lock()


def _ip_allow_load():
    out = []
    try:
        with open(IPLIST_PATH, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.append(ln)
    except Exception:
        pass
    return out


def _ip_allow_save(ips):
    body = ("# 主密钥可用的 IP 白名单（一行一个）\n"
            "# 移动网络 IP 会变，换了之后用备用密钥调 /api/trust 追加即可\n"
            + "\n".join(ips) + "\n")
    with open(IPLIST_PATH, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(IPLIST_PATH, 0o600)


def ip_allow_list():
    with _ip_lock:
        return list(_ip_allow_load())


def ip_allow_add(ip, note=""):
    """把 IP 加进白名单。返回 (是否新增, 当前条数)。"""
    if not ip or ip == "-" or ip == "?":
        return False, len(ip_allow_list())
    ip = str(ip).strip()
    try:
        ipaddress.ip_address(ip)          # 必须是合法 IP，防止注入
    except Exception:
        return False, len(ip_allow_list())
    with _ip_lock:
        ips = _ip_allow_load()
        if ip in ips:
            return False, len(ips)
        ips.append(ip)
        _ip_allow_save(ips)
        return True, len(ips)


def ip_allow_del(ip):
    with _ip_lock:
        ips = _ip_allow_load()
        if ip not in ips:
            return False, len(ips)
        ips.remove(ip)
        _ip_allow_save(ips)
        return True, len(ips)


def ip_allowed(ip):
    """主密钥能否从这个 IP 使用。未设置白名单时一律放行 ——
    否则第一次绑定之前就谁都进不来了。"""
    ips = ip_allow_list()
    if not ips:
        return True, "白名单未启用（首次绑定前）"
    if not ip or ip in ("-", "?"):
        return False, "取不到来源 IP"
    if ip in ips:
        return True, "命中白名单"
    return False, "IP 不在白名单"


def _key_match(given):
    """校验口令。返回 (是否通过, 命中的是哪个密钥)。
    hmac.compare_digest 对所有密钥逐个比，避免用简单的 in 泄露前缀信息。
    """
    if not given:
        return False, None
    g = str(given)
    for i, k in enumerate(KEYS):
        if hmac.compare_digest(g, k):
            return True, ("主密钥" if i == 0 else f"备用密钥{i}")
    return False, None


def key_allowed(given, ip):
    """口令能不能在这个 IP 上用。
    主密钥：额外要求 IP 在白名单里（本人专用）。
    备用密钥：任何 IP 都能用 —— 它本来就是给外部测试用的，
              也是 IP 变了之后给自己留的应急入口。
    返回 (是否放行, 原因)。
    """
    ok, which = _key_match(given)
    if not ok:
        return False, "口令不对"
    if which == "主密钥":
        allowed, why = ip_allowed(ip)
        if not allowed:
            return False, "主密钥已锁定你的 IP，当前 IP 未授权：" + why
    return True, which


COOKIE_NAME = "lsid"
SESSION_TTL = 12 * 3600

# 会话存 sqlite 而不是内存 dict。
# 之前放内存里，后端一重启（崩溃被守护拉起、或手动重启）所有会话就没了，
# 用户表现为"莫名其妙又要求重新输口令"。持久化之后重启无感。
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sessions.db")
_db_lock = threading.Lock()
_db = None


def _db_conn():
    global _db
    with _db_lock:
        if _db is None:
            import sqlite3
            _db = sqlite3.connect(DB_PATH, check_same_thread=False)
            _db.execute("PRAGMA journal_mode=WAL")       # 并发读不阻塞写
            _db.execute("""CREATE TABLE IF NOT EXISTS sessions(
                             token TEXT PRIMARY KEY,
                             expires REAL NOT NULL,
                             created REAL NOT NULL)""")
            _db.execute("CREATE INDEX IF NOT EXISTS ix_exp ON sessions(expires)")
            _db.commit()
        return _db


def session_put(tok, ttl=SESSION_TTL):
    now = time.time()
    c = _db_conn()
    with _db_lock:
        c.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (tok, now + ttl, now))
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        c.commit()


def session_get(tok):
    """返回 True 表示有效；过期或不存在返回 False。"""
    if not tok:
        return False
    now = time.time()
    c = _db_conn()
    with _db_lock:
        row = c.execute("SELECT expires FROM sessions WHERE token=?", (tok,)).fetchone()
        if not row:
            return False
        if row[0] <= now:
            c.execute("DELETE FROM sessions WHERE token=?", (tok,))
            c.commit()
            return False
        return True


def session_reap():
    """清掉过期会话，避免表无限膨胀。"""
    try:
        c = _db_conn()
        with _db_lock:
            c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            c.commit()
    except Exception:
        pass

RATE = {}                # client ip -> [窗口起点, {bucket: 计数}]
BOOT_TOKEN = None        # 启动时打印一次，页面用它提示需要口令

# 同源部署：前端与后端同源，浏览器默认就允许请求，不需要 CORS 响应头。
# 所以默认不发任何 Access-Control-Allow-* 头 —— 别的源发来的请求读不到响应。
# 万一以后要拆成跨域部署，把 SEND_CORS 设 True 并把具体源加进 ALLOW_ORIGINS，
# 但绝不能是 "*"（那等于让任何网页都能带着已登录的 Cookie 调后端）。
SEND_CORS = False
ALLOW_ORIGINS = set()

# 分级限流：普通 API 很宽松，/api/exec 单独收紧（它是最高风险端点）
RATE_GENERAL = 120       # 常规 API：每 IP 每分钟 120 次
RATE_EXEC = 60           # /api/exec 快速命令：每 IP 每分钟 60 次
# 慢命令（装包/编译/下载）单独一档。
# 为什么不跟快速命令共用 30 次的配额：慢命令一条要跑几分钟，
# 按"每分钟条数"限它意义不大（你一分钟也跑不了 30 条 install），
# 却会把配额和 ls/echo 抢光 —— 结果你连 ls 都被拒了。
# 真正的约束是并发数（慢通道 8 条 + 单 IP 2 条，见 BoundedThreadingServer），
# 所以这里的条数限制只防"疯狂快速提交慢命令刷满慢通道"，放宽到 20。
RATE_EXEC_SLOW = 20
RATE_INSTALL = 10        # /api/install：以 root 跑 apt/pip，每 IP 每分钟 10 次
RATE_LOGIN = 20          # 试口令：防爆破
# 8 位口令只有 62^8 组合，单纯按 IP 限流挡不住轮换 IP 的扫描器
# （实测有外部扫描器用 10 个 IP 轮换猜口令）。所以再加一层全局闸门：
# 全站累计登录失败超过阈值后，所有人一起被锁，且等待时间指数增长。
LOGIN_FAIL_GLOBAL = 0
LOGIN_FAIL_MAX = 12            # 全站累计失败上限
LOGIN_LOCK_UNTIL = 0.0
LOGIN_BACKOFF_BASE = 15         # 首次锁定 15 秒，每翻一倍再乘 2
_fb_lock_level = 0
_fb_lock = threading.Lock()
RATE_WINDOW = 60
# RATE 的定期清理：避免长期运行后内存无限增长
_LAST_SWEEP = 0


def _force_https(handler):
    """http 访问 301 到 https：录音/定位/摄像头等 API 只在 secure context 可用。"""
    if handler.headers.get("X-Forwarded-Proto", "") == "http":
        host = handler.headers.get("Host", "")
        if host:
            handler.send_response(301)
            handler.send_header("Location", "https://" + host + handler.path)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
    return False


def _client_ip(self):
    # 隧道后面所有请求都来自 127.0.0.1，X-Forwarded-For 才是真实客户端。
    # 只信任它的第一段（Cloudflare 追加的是真实 IP）。
    fwd = self.headers.get("CF-Connecting-IP", "") or self.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return self.client_address[0] if self.client_address else "?"


def record_online_ip(handler):
    """把上线的真实客户端 IP 追加记录到日志报告（跳过本地探测）。"""
    try:
        ip = _client_ip(handler)
        if ip in ("127.0.0.1", "::1", "localhost", "?"):
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        ua = (handler.headers.get("User-Agent", "") or "")[:100]
        path = handler.path.split("?")[0][:160]
        with open("/workspace/linux-sim/上线IP日志.txt", "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{ip}\t{handler.command}\t{path}\t{ua}\n")
    except Exception:
        pass


# ---- 审计日志 ----
# 之前 log_message 被整个禁掉了，出了"谁用了我的口令"这类问题无从查证。
# 这里只记安全相关事件（登录成败、提权尝试、限流、拒绝），不记普通请求，
# 免得日志把磁盘写满 —— /api/exec 一分钟就能来 30 条。
AUDIT_PATH = os.environ.get("LINUXSIM_AUDIT") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".audit.log")
_AUDIT_LOCK = threading.Lock()
_AUDIT_SINCE = 0          # 进程启动时间，用来标注重启分界


# 本机经 Cloudflare 回来的出口 IP。沙箱是多出口 NAT，每次请求可能是不同 IP，
# 记下来才能在审计里区分"这是我自己（自动化/浏览器测试）"还是"外部访客"。
# 之前就是因为没这个基准，把自己的测试流量误判成了外部扫描器。
_SELF_IPS = set()
_SELF_NETS = []          # 自己出口所在的 /24 网段（NAT 出口会轮换）


def _refresh_self_ips():
    """采自己的公网出口 IP，并记下所在 /24 网段。

    为什么必须记网段：沙箱是多出口 NAT（实测同时给出 49.235.180.191、
    124.221.146.7、1.15.114.95 三个地址），同一台机器换个出口就是另一个 IP。
    只记单个 IP 的话，隔几条请求自己人就认不出来了。
    """
    for u in ("https://api.ipify.org", "https://icanhazip.com",
              "https://ipinfo.io/ip", "https://ifconfig.me/ip"):
        try:
            r = urllib.request.urlopen(u, timeout=6).read().decode().strip()
            if re.fullmatch(r"[0-9a-fA-F:.]+", r):
                _SELF_IPS.add(r)
                try:
                    if ipaddress.ip_address(r).version == 4:
                        _SELF_NETS.append(ipaddress.ip_network(
                            r.rsplit(".", 1)[0] + ".0/24", strict=False))
                except Exception:
                    pass
        except Exception:
            pass


def _is_self(ip):
    """这个来源 IP 是本机/我自己（自动化测试、浏览器验证）吗？"""
    if ip in _SELF_IPS:
        return True
    try:
        a = ipaddress.ip_address(ip)
        return any(a in n for n in _SELF_NETS)
    except Exception:
        return False


def audit(self, event, detail=""):
    """写一行审计记录：时间 IP 事件 细节。返回格式化后的行（便于测试断言）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    ip = _client_ip(self) if self is not None else "-"
    if _is_self(ip):
        ip += " [自己]"
    ua = ""
    try:
        ua = (self.headers.get("User-Agent", "") or "")[:60]
    except Exception:
        pass
    line = f"{ts} ip={ip} {event}"
    if detail:
        line += f" {detail}"
    if ua:
        line += f' ua="{ua}"'
    try:
        with _AUDIT_LOCK:
            # 只留最近 2000 行，防止无限增长
            try:
                if os.path.exists(AUDIT_PATH) and os.path.getsize(AUDIT_PATH) > 512 * 1024:
                    with open(AUDIT_PATH, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()[-1500:]
                    with open(AUDIT_PATH, "w", encoding="utf-8") as f:
                        f.writelines(lines)
            except Exception:
                pass
            with open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    return line


def audit_query(self, kind="", n=40):
    """给 /api/logs 或本地排查用：读回最近的审计记录。"""
    try:
        with _AUDIT_LOCK:
            if not os.path.exists(AUDIT_PATH):
                return []
            with open(AUDIT_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = [x.rstrip("\n") for x in f.readlines()]
    except Exception as e:
        return [str(e)]
    if kind:
        lines = [x for x in lines if kind in x]
    return lines[-n:]


# ---- IP 封禁 ----
# 用户要求：1 秒内试密钥超过 5 次就封那个 IP。
# 这是"突发检测"，跟按分钟窗口的平均限流是两回事 —— 爆破工具的典型
# 特征就是打得很快，60 秒窗口的平均值可能还没超，但一秒内 5 次已经很可疑了。
BAN_MAX_PER_SEC = 5         # 1 秒内超过这个次数就封
BAN_WINDOW = 1.0            # 突发检测窗口（秒）
BAN_TTL = 900               # 封禁时长 15 分钟
BAN_TTL_MAX = 6 * 3600      # 累犯最多封 6 小时
_BANS = {}                  # ip -> 解封时间戳
_BAN_HITS = {}              # ip -> 累计被封次数（决定封多久）
_ban_lock = threading.Lock()

# 永不解封的名单：自己的出口 IP + 已授权的主密钥白名单 IP。
# 不加这个的话，手滑连输错 5 次会把自己封 15 分钟，非常难受。
BAN_EXEMPT = set()


def _refresh_ban_exempt():
    BAN_EXEMPT.clear()
    BAN_EXEMPT.update(_SELF_IPS)
    for n in _SELF_NETS:
        try:
            for i in range(1, 255):
                BAN_EXEMPT.add(str(n.network_address + i))
        except Exception:
            pass
    BAN_EXEMPT.update(ip_allow_list())


def ban_check(ip):
    """这个 IP 是否被封着。返回 (是否封禁, 剩余秒数)。"""
    with _ban_lock:
        until = _BANS.get(ip)
        if not until:
            return False, 0
        if time.time() > until:
            _BANS.pop(ip, None)
            return False, 0
        return True, int(until - time.time()) + 1


def ban_hit(ip):
    """记录一次可疑尝试，1 秒内超 BAN_MAX_PER_SEC 就封。返回 (是否新封, 封多久秒)。"""
    # 自己的出口 IP 和主密钥白名单 IP 永不受封。
    # 即便 BAN_EXEMPT 还没采全，_is_self / 白名单文件这两道也能兜住
    if _is_self(ip) or ip in ("-", "?", "127.0.0.1", "::1"):
        return False, 0
    if ip in ip_allow_list():
        return False, 0
    if ip in BAN_EXEMPT:
        return False, 0
    now = time.time()
    with _ban_lock:
        # 先看有没有正在封的
        until = _BANS.get(ip)
        if until and now < until:
            return False, 0
        times = _b_hist.setdefault(ip, [])
        while times and now - times[0] > BAN_WINDOW:
            times.pop(0)
        times.append(now)
        if len(times) <= BAN_MAX_PER_SEC:
            return False, 0
        # 超阈值 → 封禁，时长随累犯次数递增
        n = _BAN_HITS.get(ip, 0) + 1
        _BAN_HITS[ip] = n
        ttl = min(BAN_TTL * n, BAN_TTL_MAX)
        _BANS[ip] = now + ttl
        _b_hist.pop(ip, None)
        return True, ttl


_b_hist = {}


def _rate_ok(self, bucket="general"):
    """滑动窗口限流。返回 (是否放行, 剩余秒数)"""
    global _LAST_SWEEP
    now = time.time()
    if now - _LAST_SWEEP > 300:
        _LAST_SWEEP = now
        for ip in [k for k, v in RATE.items() if now - v[0] > RATE_WINDOW * 3]:
            RATE.pop(ip, None)
        for ip in [k for k, v in list(_BANS.items()) if now > v]:
            _BANS.pop(ip, None)

    lim = {"exec": RATE_EXEC, "exec_slow": RATE_EXEC_SLOW,
           "install": RATE_INSTALL, "login": RATE_LOGIN}.get(bucket, RATE_GENERAL)
    slot = RATE.get(self_key := _client_ip(self))
    if not slot or now - slot[0] > RATE_WINDOW:
        RATE[self_key] = [now, {bucket: 1}]
        return True, 0
    counts = slot[1]
    counts[bucket] = counts.get(bucket, 0) + 1
    if counts[bucket] > lim:
        return False, int(RATE_WINDOW - (now - slot[0])) + 1
    return True, 0


def _token_from_request(self, body_key=None):
    """依次从 Authorization 头、Cookie、body 里取凭证。"""
    h = self.headers.get("Authorization", "")
    if h.lower().startswith("bearer "):
        t = h[7:].strip()
        if t and session_get(t):
            return t
        # Bearer 后面直接就是主口令的情况（curl 调试方便）
        if t and key_allowed(t, _client_ip(self))[0]:
            return "master"
    raw = self.headers.get("Cookie", "")
    for kv in raw.split(";"):
        k, _, v = kv.strip().partition("=")
        if k == COOKIE_NAME and session_get(v):
            return v
    if body_key and key_allowed(body_key, _client_ip(self))[0]:
        return "master"
    return None


def _authed(self, body_key=None):
    return _token_from_request(self, body_key) is not None


# ---- 反馈接口限流（公开匿名提交，防刷）----
_fb_lock = threading.Lock()
_fb_hits = {}  # ip -> [timestamp, ...]


def _fb_allowed(ip):
    """每个 IP 10 分钟内最多 15 条反馈，超出则拒绝（防刷/防滥用）。"""
    now = time.time()
    with _fb_lock:
        hits = _fb_hits.get(ip, [])
        hits = [t for t in hits if now - t < 600]
        if len(hits) >= 15:
            _fb_hits[ip] = hits
            return False
        hits.append(now)
        _fb_hits[ip] = hits
    return True


# ---- 站点版本（「检查更新」数据源）----

def _fb_query(ip):
    """查当前 IP（IPv6 /64 组）的历史反馈与回复。"""
    grp = _ip_group(ip)
    out = []
    try:
        for line in open(os.path.join(_here, "feedback", "feedback.jsonl"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if _ip_group(r.get("ip", "")) == grp:
                out.append({
                    "ts": r.get("ts", ""), "title": r.get("title", ""),
                    "type": r.get("type", ""), "fixed": bool(r.get("fixed")),
                    "detail": (r.get("detail", "") or "")[:120],
                    "reply": (r.get("reply", "") or "")[:400],
                })
    except Exception:
        pass
    out.reverse()   # 新的在前
    return {"ok": True, "count": len(out), "list": out}


def _ip_group(ip):
    """反馈归属组：IPv6 按 /64 前缀（家宽前缀稳定，IP 后段会轮换），IPv4 精确。"""
    try:
        a = ipaddress.ip_address(ip)
        if a.version == 6:
            net = ipaddress.ip_network(f"{a}/64", strict=False)
            return "v6:" + str(net.network_address) + "/64"
        return "v4:" + str(a)
    except Exception:
        return "raw:" + (ip or "")



# ---- 聊天室（公共房间：文字 + 图片/视频附件 + 历史）----
ROOM_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback", "chatroom.jsonl")
_room_lock = threading.Lock()

def _room_send(data, handler):
    nick = str(data.get("nick", ""))[:24] or "访客"
    if _nick_status(nick) != "approved":
        return {"ok": False, "err": "昵称未通过审核（审核中或未提交），暂不能发言"}
    text = str(data.get("text", ""))[:2000]
    f = data.get("file") or {}
    fname = str(f.get("name", ""))[:120] if isinstance(f, dict) else ""
    if not text and not fname:
        return {"ok": False, "err": "内容不能为空"}
    if fname and ("/" in fname or ".." in fname):
        return {"ok": False, "err": "附件名不合法"}
    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "win", "room_files", fname) if fname else ""
    if fname and not os.path.isfile(fpath):
        return {"ok": False, "err": "附件未上传成功，请重试"}
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    kind = ("audio" if (fname.startswith("voice-") or ext in ("mp3","m4a","aac","wav"))
            else "img" if ext in ("png","jpg","jpeg","gif","webp")
            else "video" if ext in ("mp4","webm","ogg","mov")
            else ("file" if fname else ""))
    grp = _ip_group(_client_ip(handler))
    with _room_lock:
        try:
            last_id = 0
            if os.path.isfile(ROOM_LOG):
                with open(ROOM_LOG, encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            try: last_id = json.loads(line).get("id", 0)
                            except Exception: pass
            rec = {"id": last_id + 1, "ts": time.strftime("%m-%d %H:%M:%S"), "nick": nick, "grp": grp[-12:], "text": text}
            loc = data.get("loc")
            if isinstance(loc, dict) and "lat" in loc and "lon" in loc:
                try:
                    rec["loc"] = {"lat": round(float(loc["lat"]), 6),
                                  "lon": round(float(loc["lon"]), 6),
                                  "acc": int(float(loc.get("acc", 0)))}
                except Exception:
                    pass
            if fname:
                rec["file"] = {"name": fname, "url": "/win/room_files/" + fname, "kind": kind, "size": os.path.getsize(fpath)}
            with open(ROOM_LOG, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(json.dumps(rec, ensure_ascii=False) + chr(10))
            reg_event("CHATROOM", nick + "：" + (text[:40] or ("[附件] " + fname)))
            try:
                devid = str(data.get("devid", ""))[:40] or (nick + "|" + grp)
                _fc = _fconn()
                _fc.execute("INSERT INTO users(nick,grp,last_seen,devid) VALUES(?,?,?,?) "
                            "ON CONFLICT(devid) DO UPDATE SET nick=excluded.nick, grp=excluded.grp, last_seen=excluded.last_seen",
                            (nick, grp, time.strftime("%Y-%m-%d %H:%M:%S"), devid))
                _fc.commit(); _fc.close()
            except Exception: pass
        except Exception as e:
            return {"ok": False, "err": str(e)}
    return {"ok": True}

def _room_msgs(data):
    since = int(data.get("since", 0) or 0)
    out = []
    try:
        if os.path.isfile(ROOM_LOG):
            with open(ROOM_LOG, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip(): continue
                    try: r = json.loads(line)
                    except Exception: continue
                    if r.get("id", 0) > since:
                        out.append(r)
    except Exception:
        pass
    return {"ok": True, "msgs": out[-100:]}



# ---- 昵称/消息违规词过滤（骂人/侮辱类）----
BAD_WORDS = ["傻逼","煞笔","沙比","妈的","他妈","你妈","艹","操你","日你","妈逼","贱人","婊子","狗日","王八蛋","龟孙","憨批","脑残","废物","滚蛋","畜生","杂种","下贱","nmsl","cnm","sb ","fuck","shit","bitch","asshole","dick","cunt"]

def _has_bad_word(t):
    t = (t or "").lower()
    return any(w in t for w in BAD_WORDS)



# ---- 昵称审核制：设置昵称 → 管理台批准 → 才能用于聊天/好友 ----
def _nick_status(nick):
    try:
        with _friend_lock:
            c = _fconn()
            c.execute("CREATE TABLE IF NOT EXISTS nick_status(nick TEXT PRIMARY KEY, grp TEXT, status TEXT, ts TEXT)")
            row = c.execute("SELECT status FROM nick_status WHERE nick=?", (nick,)).fetchone()
            c.close()
            return row[0] if row else None
    except Exception:
        return None

def _nick_api(data, handler):
    act = str(data.get("action", ""))
    nick = str(data.get("nick", ""))[:24].strip()
    grp = _ip_group(_client_ip(handler))
    if act == "set":
        if not nick: return {"ok": False, "err": "昵称不能为空"}
        # AI 预筛：违规词直接拒；可疑特征标注供人工参考；其余进人工队列
        if _has_bad_word(nick):
            return {"ok": False, "status": "rejected", "err": "昵称包含违规词，已被自动拒绝，换一个再提交"}
        digits = sum(ch.isdigit() for ch in nick)
        ai_note = "正常"
        if len(nick) < 2: ai_note = "可疑：过短"
        elif digits > len(nick) * 0.6: ai_note = "可疑：数字占比过高"
        elif any(nick.count(ch) >= 4 for ch in nick): ai_note = "可疑：字符堆砌"
        elif all(not ("\u4e00" <= ch <= "\u9fff") and not ch.isalpha() for ch in nick): ai_note = "可疑：无有效文字"
        st = _nick_status(nick)
        if st == "approved": return {"ok": True, "status": "approved"}
        try:
            with _friend_lock:
                c = _fconn()
                c.execute("CREATE TABLE IF NOT EXISTS nick_status(nick TEXT PRIMARY KEY, grp TEXT, status TEXT, ts TEXT)")
                try: c.execute("ALTER TABLE nick_status ADD COLUMN ai_note TEXT")
                except Exception: pass
                c.execute("INSERT INTO nick_status(nick,grp,status,ts,ai_note) VALUES(?,?,?,?,?) ON CONFLICT(nick) DO UPDATE SET grp=excluded.grp, status=excluded.status, ts=excluded.ts, ai_note=excluded.ai_note",
                          (nick, grp, "pending", time.strftime("%m-%d %H:%M:%S"), ai_note))
                c.commit(); c.close()
            reg_event("NICK_REQ", "昵称申请：" + nick + "（AI：" + ai_note + "）")
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "status": "pending", "ai": ai_note}
    if act == "check":
        return {"ok": True, "status": _nick_status(nick)}
    if act == "recall":  # 按设备 ID 找回自己的昵称（http→https 切换后 localStorage 隔离时用）
        devid = str(data.get("devid", ""))[:40]
        if devid:
            try:
                with _friend_lock:
                    c = _fconn()
                    row = c.execute("SELECT nick,grp FROM users WHERE devid=? ORDER BY last_seen DESC LIMIT 1",
                                    (devid,)).fetchone()
                    c.close()
                if row and _nick_status(row[0]) == "approved":
                    return {"ok": True, "nick": row[0], "status": "approved"}
            except Exception:
                pass
        return {"ok": True, "nick": None}
    if act == "auto":   # 系统随机分发昵称（系统生成的名字天然合规，直接过审）
        adj=["疾风","星尘","月影","晨光","青柠","暖阳","云游","萤火","薄荷","墨白","清风","溪流","松果","橘猫","银河"]
        noun=["旅人","行者","探险家","观测者","骑士","诗人","旅鸟","船长","学徒","守望者"]
        newn = ""
        for _ in range(20):
            cand = random.choice(adj) + random.choice(noun) + str(random.randint(10, 999))
            if _nick_status(cand) is None:
                newn = cand; break
        if not newn: newn = "用户" + str(random.randint(10000, 99999))
        try:
            with _friend_lock:
                c = _fconn()
                c.execute("CREATE TABLE IF NOT EXISTS nick_status(nick TEXT PRIMARY KEY, grp TEXT, status TEXT, ts TEXT)")
                c.execute("INSERT INTO nick_status(nick,grp,status,ts,ai_note) VALUES(?,?,?,?,?) ON CONFLICT(nick) DO UPDATE SET status=excluded.status, ts=excluded.ts",
                          (newn, grp, "approved", time.strftime("%m-%d %H:%M:%S"), "系统分发"))
                c.commit(); c.close()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "nick": newn, "status": "approved"}
    if act == "all":
        st = _nick_status(None)  # 触发表存在性（防御）
        try:
            with _friend_lock:
                c = _fconn()
                rows = [{"nick": r[0], "grp": r[1], "status": r[2], "ts": r[3]}
                        for r in c.execute("SELECT nick,grp,status,ts FROM nick_status ORDER BY ts DESC LIMIT 50")]
                try: c.execute("ALTER TABLE nick_status ADD COLUMN ai_note TEXT")
                except Exception: pass
                c.close()
            return {"ok": True, "list": rows}
        except Exception as e:
            return {"ok": True, "list": []}
    if act == "approve":
        # 仅管理台（admin_server 直接写库），主站不放行
        return {"ok": False, "err": "仅供管理台使用"}
    return {"ok": False, "err": "action 不合法"}


# ---- 好友与私聊（身份=昵称+网络组；数据落 sqlite/jsonl）----
FRIEND_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback", "friends.db")
DM_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback", "dm.jsonl")
_friend_lock = threading.Lock()

def _fconn():
    import sqlite3 as _s2
    c = _s2.connect(FRIEND_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS users(nick TEXT, grp TEXT, last_seen TEXT)")
    try: c.execute("ALTER TABLE users ADD COLUMN devid TEXT")
    except Exception: pass
    try: c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_devid ON users(devid)")
    except Exception: pass
    c.execute("CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, fn TEXT, fg TEXT, tn TEXT, ts TEXT, status TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS friends(a_n TEXT, a_g TEXT, b_n TEXT, b_g TEXT, ts TEXT)")
    return c

def _uid_of(nick, handler):
    return (str(nick)[:24], _ip_group(_client_ip(handler)))

def _friend_api(data, handler):
    act = str(data.get("action", ""))
    nick, grp = data.get("_uid", ("", ""))
    if act == "request":
        if _nick_status(nick) != "approved":
            return {"ok": False, "err": "你的昵称未通过审核，暂不能加好友"}
        if _has_bad_word(str(data.get("to", ""))):
            return {"ok": False, "err": "对方昵称违规，无法添加"}
    if act == "seen":   # 登记在线用户（按设备 ID 唯一；手机/电脑各自一条）
        devid = str(data.get("devid", ""))[:40] or (nick + "|" + grp)
        try:
            with _friend_lock:
                c = _fconn()
                c.execute("INSERT INTO users(nick,grp,last_seen,devid) VALUES(?,?,?,?) "
                          "ON CONFLICT(devid) DO UPDATE SET nick=excluded.nick, grp=excluded.grp, last_seen=excluded.last_seen",
                          (nick, grp, time.strftime("%Y-%m-%d %H:%M:%S"), devid))
                c.commit(); c.close()
        except Exception: pass
        return {"ok": True}
    if act == "users":  # 可加好友的人（按设备区分；排除自己这台设备）
        devid = str(data.get("devid", ""))[:40]
        out = []
        try:
            with _friend_lock:
                c = _fconn()
                for r in c.execute("SELECT nick,grp,last_seen,devid FROM users ORDER BY last_seen DESC LIMIT 60"):
                    if devid and r[3] == devid:
                        continue                      # 自己这台设备
                    if r[0] == nick:
                        continue                      # 同昵称（多半是自己在别的浏览器）
                    out.append({"nick": r[0], "grp": (r[1] or "")[-10:], "seen": r[2]})
                c.close()
        except Exception: pass
        return {"ok": True, "users": out}
    if act == "request":   # 发好友申请
        tn = str(data.get("to", ""))[:24]
        if not tn: return {"ok": False, "err": "缺少对方昵称"}
        try:
            with _friend_lock:
                c = _fconn()
                dup = c.execute("SELECT id FROM requests WHERE fn=? AND fg=? AND tn=? AND status='pending'",
                                (nick, grp, tn)).fetchone()
                if dup:
                    c.close(); return {"ok": True, "msg": "申请已发过，等对方处理"}
                c.execute("INSERT INTO requests(fn,fg,tn,ts,status) VALUES(?,?,?,?,?)",
                          (nick, grp, tn, time.strftime("%m-%d %H:%M:%S"), "pending"))
                c.commit(); c.close()
            reg_event("FRIEND_REQ", nick + " → " + tn)
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "msg": "好友申请已发送"}
    if act == "requests":  # 我的待处理申请（发给）+ 我收到的
        mine, got = [], []
        try:
            with _friend_lock:
                c = _fconn()
                for r in c.execute("SELECT id,fn,fg,ts FROM requests WHERE tn=? AND status='pending' ORDER BY id DESC", (nick,)):
                    got.append({"id": r[0], "nick": r[1], "grp": r[2], "ts": r[3]})
                for r in c.execute("SELECT id,tn,ts,status FROM requests WHERE fn=? ORDER BY id DESC LIMIT 20", (nick,)):
                    mine.append({"id": r[0], "nick": r[1], "ts": r[2], "status": r[3]})
                c.close()
        except Exception: pass
        return {"ok": True, "got": got, "mine": mine}
    if act == "accept":    # 同意申请
        rid = int(data.get("id", 0) or 0)
        try:
            with _friend_lock:
                c = _fconn()
                row = c.execute("SELECT fn,fg FROM requests WHERE id=? AND tn=? AND status='pending'", (rid, nick)).fetchone()
                if not row:
                    c.close(); return {"ok": False, "err": "申请不存在或已处理"}
                c.execute("UPDATE requests SET status='accepted' WHERE id=?", (rid,))
                c.execute("INSERT INTO friends(a_n,a_g,b_n,b_g,ts) VALUES(?,?,?,?,?)",
                          (nick, grp, row[0], row[1], time.strftime("%m-%d %H:%M:%S")))
                c.commit(); c.close()
            reg_event("FRIEND_OK", nick + " + " + row[0])
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": True, "msg": "已添加好友 " + row[0]}
    if act == "list":      # 好友列表（含在线状态：90 秒内有心跳=在线）
        out = []
        try:
            now = time.time()
            seen = {}
            with _friend_lock:
                c = _fconn()
                for r in c.execute("SELECT nick,grp,last_seen FROM users"):
                    try:
                        seen[(r[0], r[1])] = time.mktime(time.strptime(r[2], "%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        pass
                for r in c.execute("SELECT a_n,a_g,b_n,b_g FROM friends WHERE a_n=? OR b_n=?", (nick, nick)):
                    peer_n, peer_g = (r[2], r[3]) if r[0] == nick else (r[0], r[1])
                    on = any(k[0] == peer_n and now - v < 90 for (k, v) in seen.items() if k[0] == peer_n)
                    out.append({"nick": peer_n, "grp": peer_g[-10:], "online": on})
                c.close()
        except Exception: pass
        return {"ok": True, "friends": out}
    return {"ok": False, "err": "action 不合法"}

def _dm_pair(a_n, a_g, b_n, b_g):
    p = sorted([a_n + "|" + a_g, b_n + "|" + b_g])
    return p[0] + "||" + p[1]

def _dm_send(data, handler):
    to = str(data.get("to", ""))[:24]
    nick, grp = data.get("_uid", ("", ""))
    text = str(data.get("text", ""))[:2000]
    if _nick_status(nick) != "approved":
        return {"ok": False, "err": "你的昵称未通过审核，暂不能私聊"}
    if _has_bad_word(text):
        return {"ok": False, "err": "消息包含违规词，请文明发言"}
    loc = data.get("loc")
    f = data.get("file") or {}
    fname = str(f.get("name", ""))[:120] if isinstance(f, dict) else ""
    if not to or (not text and not fname and not (isinstance(loc, dict) and "lat" in loc)):
        return {"ok": False, "err": "内容不能为空"}
    if fname and ("/" in fname or ".." in fname):
        return {"ok": False, "err": "附件名不合法"}
    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "win", "room_files", fname) if fname else ""
    if fname and not os.path.isfile(fpath):
        return {"ok": False, "err": "附件未上传成功"}
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    kind = ("audio" if (fname.startswith("voice-") or ext in ("mp3","m4a","aac","wav"))
            else "img" if ext in ("png","jpg","jpeg","gif","webp")
            else "video" if ext in ("mp4","webm","ogg","mov")
            else ("file" if fname else ""))
    rec = {"ts": time.strftime("%m-%d %H:%M:%S"), "from": nick, "to": to, "text": text,
           "pair": _dm_pair(nick, grp, to, grp)}
    if isinstance(loc, dict) and "lat" in loc:
        try: rec["loc"] = {"lat": round(float(loc["lat"]),6), "lon": round(float(loc["lon"]),6), "acc": int(float(loc.get("acc",0)))}
        except Exception: pass
    if fname:
        rec["file"] = {"name": fname, "url": "/win/room_files/" + fname, "kind": kind, "size": os.path.getsize(fpath) if os.path.isfile(fpath) else 0}
    with _friend_lock:
        try:
            last = 0
            if os.path.isfile(DM_LOG):
                with open(DM_LOG, encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            try: last = json.loads(line).get("id", 0)
                            except Exception: pass
            rec["id"] = last + 1
            with open(DM_LOG, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(json.dumps(rec, ensure_ascii=False) + chr(10))
        except Exception as e:
            return {"ok": False, "err": str(e)}
    return {"ok": True}

def _dm_msgs(data, handler):
    peer = str(data.get("peer", ""))[:24]
    since = int(data.get("since", 0) or 0)
    nick, grp = data.get("_uid", ("", ""))
    pair = _dm_pair(nick, grp, peer, grp)
    out = []
    try:
        if os.path.isfile(DM_LOG):
            with open(DM_LOG, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip(): continue
                    try: r = json.loads(line)
                    except Exception: continue
                    if r.get("pair") == pair and r.get("id", 0) > since:
                        out.append(r)
    except Exception:
        pass
    return {"ok": True, "msgs": out[-100:]}



# ---- 消息撤回（微信式：本人撤回自己的消息，正文清空显示"已撤回"）----
def _recall_jsonl(path, msg_id, owner_field, owner_val, pair_field=None, pair_val=None):
    if not os.path.isfile(path):
        return False
    lines = []
    hit = False
    with _room_lock:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if (r.get("id") == msg_id and r.get(owner_field) == owner_val
                            and (pair_field is None or r.get(pair_field) == pair_val)):
                        r["recalled"] = True
                        r["text"] = ""
                        r.pop("file", None)
                        r.pop("loc", None)
                        hit = True
                    lines.append(json.dumps(r, ensure_ascii=False))
            if hit:
                with open(path, "w", encoding="utf-8") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    fh.write("\n".join(lines) + "\n")
        except Exception:
            return False
    return hit


def _room_recall(data, handler):
    nick, _ = data.get("_uid", ("", ""))
    try:
        mid = int(data.get("id", 0) or 0)
    except Exception:
        return {"ok": False, "err": "参数不合法"}
    if not mid or not nick:
        return {"ok": False, "err": "参数不合法"}
    if _recall_jsonl(ROOM_LOG, mid, "nick", nick):
        reg_event("CHATROOM", nick + " 撤回了一条消息")
        return {"ok": True}
    return {"ok": False, "err": "撤回失败：只能撤回自己的消息"}


def _dm_recall(data, handler):
    nick, grp = data.get("_uid", ("", ""))
    peer = str(data.get("peer", ""))[:24]
    try:
        mid = int(data.get("id", 0) or 0)
    except Exception:
        return {"ok": False, "err": "参数不合法"}
    if not mid or not nick or not peer:
        return {"ok": False, "err": "参数不合法"}
    pair = _dm_pair(nick, grp, peer, grp)
    if _recall_jsonl(DM_LOG, mid, "from", nick, "pair", pair):
        return {"ok": True}
    return {"ok": False, "err": "撤回失败：只能撤回自己的消息"}



# ---- 音视频通话信令（WebRTC：offer/answer/ICE 候选经轮询中转）----
CALLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback", "calls.json")
_call_lock = threading.Lock()

def _calls_load():
    try:
        return json.load(open(CALLS_FILE, encoding="utf-8"))
    except Exception:
        return {}

def _calls_save(d):
    with open(CALLS_FILE, "w", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(d, f, ensure_ascii=False)

def _call_api(data, handler):
    act = str(data.get("action", ""))
    me = str(data.get("nick", ""))[:24]
    peer = str(data.get("peer", ""))[:24]
    if not me:
        return {"ok": False, "err": "未设置昵称"}
    pair = "|".join(sorted([me, peer])) if peer else ""
    now = time.time()
    with _call_lock:
        calls = _calls_load()
        # 清理 2 分钟前的残留
        calls = {k: v for k, v in calls.items() if now - v.get("ts", 0) < 120}
        if act == "invite":
            calls[pair] = {"from": me, "to": peer, "kind": "audio" if data.get("kind") != "video" else "video",
                           "state": "ringing", "ts": now, "offer": str(data.get("offer", ""))[:32000],
                           "answer": "", "ice_a": [], "ice_b": []}   # ice_a 给 callee, ice_b 给 caller
            _calls_save(calls)
            reg_event("CALL", me + " → " + peer + "（" + ("视频" if data.get("kind")=="video" else "语音") + "）")
            return {"ok": True}
        if act == "answer":
            c = calls.get(pair)
            if not c or c["state"] != "ringing":
                return {"ok": False, "err": "呼叫已失效"}
            c["answer"] = str(data.get("answer", ""))[:32000]
            c["state"] = "active"
            c["ts"] = now
            _calls_save(calls)
            return {"ok": True}
        if act == "ice":
            c = calls.get(pair)
            if not c:
                return {"ok": False}
            bucket = "ice_b" if c["from"] == me else "ice_a"   # 对方要收的候选
            c[bucket].append(str(data.get("cand", ""))[:1000])
            c["ts"] = now
            _calls_save(calls)
            return {"ok": True}
        if act == "end":
            c = calls.pop(pair, None)
            _calls_save(calls)
            return {"ok": True}
        if act == "poll":
            mine = None
            for k, c in calls.items():
                if me in (c["from"], c["to"]):
                    peer_n = c["to"] if c["from"] == me else c["from"]
                    ice_key = "ice_b" if c["from"] == me else "ice_a"  # 发给我的候选
                    mine = {"peer": peer_n, "kind": c["kind"], "state": c["state"],
                            "role": "caller" if c["from"] == me else "callee",
                            "offer": c.get("offer", ""), "answer": c.get("answer", ""),
                            "ice": c.get(ice_key, [])}
                    break
            _calls_save(calls)
            return {"ok": True, "call": mine}
    return {"ok": False, "err": "action 不合法"}


# ---- 模拟注册表（Windows 注册表思路）：键值 hive + 系统事件流，sqlite 落盘 ----
import sqlite3 as _sq3
REG_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reg.db")
_reg_lock = threading.Lock()

def _reg_conn():
    c = _sq3.connect(REG_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS hive(k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, detail TEXT)")
    return c

def reg_set(k, v):
    with _reg_lock:
        c = _reg_conn()
        c.execute("INSERT INTO hive(k,v,ts) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts",
                  (k, str(v), time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit(); c.close()

def reg_get(k):
    with _reg_lock:
        c = _reg_conn()
        row = c.execute("SELECT v, ts FROM hive WHERE k=?", (k,)).fetchone()
        c.close()
        return row

def reg_event(etype, detail=""):
    with _reg_lock:
        c = _reg_conn()
        c.execute("INSERT INTO events(ts,type,detail) VALUES(?,?,?)",
                  (time.strftime("%Y-%m-%d %H:%M:%S"), etype, str(detail)[:200]))
        c.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")
        c.commit(); c.close()

def _reg_boot():
    try:
        row = reg_get("boot_count")
        n = int(row[0]) + 1 if row else 1
        reg_set("boot_count", n); reg_set("last_boot", time.strftime("%Y-%m-%d %H:%M:%S"))
        reg_set("version", _server_version() or "?")
        reg_event("SYSTEM_BOOT", "第 %d 次启动，版本 %s" % (n, _server_version() or "?"))
    except Exception:
        pass

def _reg_api(data):
    act = str(data.get("action", ""))
    if act == "get":
        k = str(data.get("k", ""))
        row = reg_get(k)
        return {"ok": True, "k": k, "v": row[0] if row else None, "ts": row[1] if row else None}
    if act == "set":
        k, v = str(data.get("k", ""))[:80], str(data.get("v", ""))[:4000]
        if not k: return {"ok": False, "err": "键名不能为空"}
        reg_set(k, v); reg_event("REG_SET", k + " = " + v[:60])
        return {"ok": True}
    if act == "all":
        with _reg_lock:
            c = _reg_conn()
            hive = [{"k": r[0], "v": r[1], "ts": r[2]} for r in c.execute("SELECT k,v,ts FROM hive ORDER BY k")]
            events = [{"ts": r[0], "type": r[1], "detail": r[2]} for r in c.execute("SELECT ts,type,detail FROM events ORDER BY id DESC LIMIT 60")]
            c.close()
        return {"ok": True, "hive": hive, "events": events}
    return {"ok": False, "err": "action 需为 get/set/all"}


def _server_version():
    """从 changelog.js 提取服务端当前版本号。发版只改一处，接口自动跟随。"""
    try:
        s = open(os.path.join(_here, "changelog.js"), encoding="utf-8").read()
        m = re.search(r'window\.CURRENT_VERSION\s*=\s*"([^"]+)"', s)
        return m.group(1) if m else None
    except Exception:
        return None


# 真实终端身份：以系统账号 user 运行，主目录 /home/user
USER_ACCOUNT = "user"
HOME_DIR = "/home/user"
SESSIONS = {}  # sid -> 当前工作目录

# 注意：firejail 在本环境无效（沙箱机本身已在容器里，firejail 检测到
# "an existing sandbox" 后会跳过全部隔离）。实测 rlimit/--private 都不生效，
# 所以改用 bash ulimit + cgroup v2，见 user_bash。

# 启动时把沙箱初始化成真实 Ubuntu 桌面环境
try:
    _here = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["bash", os.path.join(_here, "setup_env.sh")], timeout=120,
                   capture_output=True, text=True)
except Exception as _e:
    print("[warn] setup_env 失败:", _e)

def _rewrite_html(html, base):
    """把页面里的相对链接改写为经本代理的绝对地址，供 iframe 内点击不跨域。"""
    def repl(m):
        attr, quote, val = m.group(1), m.group(2), m.group(3)
        if not val or val.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "/api/proxy")):
            return m.group(0)
        if val.startswith("//"):
            val = "https:" + val
        abs_url = urllib.parse.urljoin(base, val)
        if abs_url.startswith(("http://", "https://")):
            enc = "/api/proxy?url=" + urllib.parse.quote(abs_url, safe="")
            return f"{attr}={quote}{enc}{quote}"
        return m.group(0)
    return re.sub(r'(href|src)=(["\'])(.*?)\2', repl, html, flags=re.I)

def run_bash(cmd, timeout=15):
    """以 root 跑一条命令用于内部查询（ps/df/dmesg）。同样带硬超时。"""
    try:
        rc, ob, eb, to = _run_limited(["bash", "-c", cmd], timeout,
                                     env={**os.environ, "TERM": "xterm", "PS1": ""})
        out = ob.decode("utf-8", "replace")
        err = eb.decode("utf-8", "replace")
        if to:
            return out, f"命令执行超时（{timeout}s）"
        if rc != 0 and not err.strip():
            err = f"(退出码 {rc})"
        return out, err
    except Exception as e:
        return "", str(e)


# 允许以 root 代装的包管理器前缀。user 已经没有免密 sudo（见 setup_env.sh），
# 所以装包必须走这里，并且只接受白名单里的管理器和干净的包名。
_PKG_CMDS = ("apt-get install", "apt install", "apt-get update", "apt-get remove",
             "apt-get autoremove", "pip install", "pip3 install")
_PKG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(?:\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*$")


def install_packages(argstr, timeout=300):
    """以 root 身份装包。严格白名单：只允许 apt/pip 这几个管理器和干净的包名，
    杜绝 `apt-get install foo; rm -rf /` 这类拼接。"""
    argstr = (argstr or "").strip()
    if not argstr:
        return "", "没有指定要安装的包"
    if "\n" in argstr or "\r" in argstr:
        return "", "命令不能包含换行"
    if not _PKG_OK.match(argstr):
        return "", f"包名含非法字符，只允许字母数字和 . _ + - :，收到：{argstr[:80]}"
    if not any(argstr.startswith(c) for c in _PKG_CMDS):
        return "", "只允许 apt-get install / apt install / apt-get update / " \
                   "apt-get remove / apt-get autoremove / pip install"
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "TERM": "dumb"}
    if argstr.startswith(("pip install", "pip3 install")):
        cmd = ["pip3", "install", "--break-system-packages"] + shlex.split(argstr)[2:]
    else:
        cmd = ["bash", "-c", argstr + " -y"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        return out[-6000:], ""
    except subprocess.TimeoutExpired:
        return "", f"安装超时（{timeout}s），包太大或网络太慢"
    except Exception as e:
        return "", str(e)


CWD_MARK = "<<<__CWD__>>>"

CG_ROOT = "/sys/fs/cgroup"
CG_BASE = os.path.join(CG_ROOT, "linuxsim")
CG_MAX_PROCS = 512
CG_MAX_MEM = 3 * 1024 * 1024 * 1024      # 3GB


def _cgroup_available():
    """cgroup v2 且 pids/memory 控制器可写才用；不可用就只靠 ulimit。"""
    try:
        if not os.path.isdir(CG_BASE):
            with open(os.path.join(CG_ROOT, "cgroup.subtree_control"), "w") as f:
                f.write("+pids +memory")
        return os.path.isdir(CG_BASE)
    except Exception:
        return False


CG_OK = _cgroup_available()
_cg_seq = 0


def _cgroup_new():
    """给这次命令建一个独立 cgroup，限制进程数与内存。失败返回 None（降级到 ulimit）。"""
    global _cg_seq
    if not CG_OK:
        return None
    _cg_seq += 1
    path = os.path.join(CG_BASE, "s%d" % _cg_seq)
    try:
        os.mkdir(path)
        with open(os.path.join(path, "pids.max"), "w") as f:
            f.write(str(CG_MAX_PROCS))
        with open(os.path.join(path, "memory.max"), "w") as f:
            f.write(str(CG_MAX_MEM))
        return path
    except Exception:
        return None


def _cgroup_del(path):
    try:
        for p in os.listdir(path):
            try:
                os.kill(int(p), 9)
            except Exception:
                pass
        os.rmdir(path)
    except Exception:
        pass


def _kill_tree(p):
    """连同整个进程组杀掉。

    subprocess.run(timeout=) 只杀直接子进程，子孙会变孤儿继续跑 ——
    `yes` 或 fork bomb 能把机器跑挂，必须按进程组杀
    （start_new_session=True 让它自成一会话）。
    """
    import signal
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _drain(pipe, cap, sink):
    """从 pipe 读最多 cap 字节进 sink，剩下的一律丢掉。

    为什么必须限量：subprocess.communicate() 会把子进程输出**全部**读进
    Python 内存。像 `yes` / `cat /dev/urandom` 这种命令每秒能吐几百 MB，
    几秒就能把后端进程自己撑爆 —— 表现是没有任何堆栈的直接消失（被 OOM
    硬杀），连日志都留不下。之前用户报告的"又崩了"就是这个。
    """
    got = 0
    try:
        while got < cap:
            b = pipe.read(65536)
            if not b:
                break
            sink.append(b)
            got += len(b)
        # 超额部分必须继续读走，否则子进程写满管道阻塞、永远等不到退出。
        # 边读边丢，不留在内存里。
        over = pipe.read(1 << 20)
        while over:
            over = pipe.read(1 << 20)
    except Exception:
        pass
    return got


def _run_limited(argv, timeout, env=None, cwd=None, cgroup=None,
                 out_cap=1024 * 1024):
    """带硬超时的执行：超时就 kill 整个进程组。返回 (rc, stdout, stderr, 是否超时)

    输出双通道限量读取（默认各 1MB），绝不把子进程输出无限堆进内存。
    """
    pre = (lambda: open(os.path.join(cgroup, "cgroup.procs"), "w")) if cgroup else None
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, env=env, cwd=cwd,
                         start_new_session=True)   # 自成进程组，便于整组杀
    if pre:
        try:
            f = pre(); f.write(str(p.pid)); f.close()
        except Exception:
            pass
    ob, eb = [], []
    th_o = threading.Thread(target=_drain, args=(p.stdout, out_cap, ob), daemon=True)
    th_e = threading.Thread(target=_drain, args=(p.stderr, 64 * 1024, eb), daemon=True)
    th_o.start(); th_e.start()
    try:
        p.wait(timeout=timeout)
        th_o.join(timeout=3); th_e.join(timeout=3)
        return p.returncode, b"".join(ob), b"".join(eb), False
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        th_o.join(timeout=2); th_e.join(timeout=2)
        return -9, b"".join(ob), b"".join(eb), True


def _needs_slow_timeout(cmd):
    """判断这条命令是否属于"本来就该慢"的那一类。

    为什么要有分级：一刀切 30s 有两个方向的错。
      · 短的给多了 —— 你敲 ls 却要等 30 秒才报错，手机上体验极差
      · 长的给少了 —— apt update / make / pip install 必然被腰斩，
        装一半的包留在半成品状态，比直接失败还难收拾
    所以按命令特征分档，而不是让用户自己选。

    判据只认"明确耗时"的动作（包管理、编译、大文件、下载、网络），
    拿不准一律按快的算 —— 宁可超时提醒用户，也不会莫名其妙卡 5 分钟。
    """
    c = re.sub(r"[;#].*", "", (cmd or "")).strip().lower()   # 去掉注释和后半段
    c = re.sub(r"^\s*(sudo|runuser|env|time|nohup|xargs)\s+", "", c)
    slow_markers = (
        # 包管理：apt/pip 要联网，最慢的一档
        "apt-get", "apt ", "aptitude", "dpkg", "pip install", "pip3 install",
        "pip download", "npm install", "pnpm install", "yarn add", "cargo ",
        "go get", "go build", "gem install", "snap install", "flatpak",
        # 编译
        "make", "gcc", "g++", "cc ", "clang", "cmake", "ninja", "ld ",
        "configure", "mvn ", "gradle", "tsc", "javac", "rustc", "cargo",
        # 大文件 / 磁盘密集
        "tar ", "unzip", "7z", "gzip", "zip ", "dd if", "rsync", "cp -r",
        "mkfs", "badblocks", "fdisk", "parted", "shred", "convert ",
        # 网络（跨境线路可能很慢）
        "curl ", "wget ", "git clone", "git pull", "git fetch", "ssh ",
        "scp ", "rsync", "nc ", "nmap", "ping ",
        # 长跑着的
        "sleep ", "tail -f", "watch ", "top", "htop", "vim", "nano",
    )
    return any(m in c for m in slow_markers)


# exec 超时三档。名字里的数字是秒，前端据此决定等多久。
TIMEOUT_FAST = 15        # ls / echo / cat 这类，秒回
TIMEOUT_SLOW = 300       # 编译 / 装包 / 压缩 / 传文件
# 前端兜底：必须比后端慢档再长一点，否则前端先放弃、
# 用户看到"无法连接后端"而命令其实还在后端跑着（这个错配踩过）
FRONTEND_GRACE = 8


def exec_timeout_for(cmd):
    """返回 (后端超时秒数, 是否慢命令)。"""
    return (TIMEOUT_SLOW, True) if _needs_slow_timeout(cmd) else (TIMEOUT_FAST, False)


def user_bash(cmd, cwd=None, sid="default", timeout=None):
    """以系统用户 user 身份执行命令，并保持工作目录（cd 生效）。

    timeout 传 None 表示按命令特征自动选档（推荐）；
    显式传数字则强制用那个秒数。
    """
    if timeout is None:
        timeout, _slow = exec_timeout_for(cmd)
    # 提权尝试留痕。sudo 本身已经被 setup_env.sh 摘掉了免密规则，
    # 这里只是记一笔，方便事后看出是谁在试。
    if re.search(r'\b(sudo|su|doas|pkexec)\b', cmd or ""):
        flat = re.sub(r"\s+", " ", cmd)[:120]
        audit(None, "PRIV_ESC", "cmd=%r sid=%s" % (flat, sid))
    cwd = cwd or SESSIONS.get(sid) or HOME_DIR
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        cwd = HOME_DIR
    env = {**os.environ, "TERM": "xterm", "HOME": HOME_DIR,
           "USER": USER_ACCOUNT, "LOGNAME": USER_ACCOUNT}
    # 隔离说明：firejail 在本环境不可用 —— 沙箱机本身已在容器里，
    # firejail 一检测到 "an existing sandbox was detected" 就直接放弃所有
    # 隔离（实测 rlimit/--private 全部不生效）。所以这里改用两道不依赖
    # 它的硬限制，都能实测拦住：
    #   1) 前置 bash ulimit：限制进程数/地址空间/文件大小/打开文件数
    #   2) cgroup v2 pids+memory：内核层硬限制，bash 自己也逃不掉
    argv = ["runuser", "-u", USER_ACCOUNT, "--", "/bin/bash", "--noprofile", "--norc", "-c"]
    guard = (
        # 进程数 256：挡住 fork bomb
        "ulimit -u 256 2>/dev/null; "
        # 地址空间 2GB：挡住 while true 疯狂吃内存
        "ulimit -v 2097152 2>/dev/null; "
        # 单文件 512MB：挡住 dd 把磁盘写满
        "ulimit -f 524288 2>/dev/null; "
        # 打开文件数 2048
        "ulimit -n 2048 2>/dev/null; "
        # 核心转储关闭（避免泄露内存内容）
        "ulimit -c 0 2>/dev/null; "
        f"cd {shlex.quote(cwd)} 2>/dev/null || cd {shlex.quote(HOME_DIR)}\n"
        f"{cmd}\n"
        f'printf "\\n{CWD_MARK}%s" "$PWD"\n'
    )
    argv.append(guard)

    cg = _cgroup_new()
    try:
        if cg:
            rc, ob, eb, timedout = _run_limited(argv, timeout, env=env, cgroup=cg)
        else:
            rc, ob, eb, timedout = _run_limited(argv, timeout, env=env)
        out = ob.decode("utf-8", "replace")
        err = eb.decode("utf-8", "replace")
        newcwd = cwd
        i = out.rfind(CWD_MARK)
        if i != -1:
            newcwd = (out[i + len(CWD_MARK):].strip().splitlines() or [cwd])[0] or cwd
            out = out[:i].rstrip("\n")
        SESSIONS[sid] = newcwd
        if timedout:
            return out, f"命令执行超时（{timeout}s），已强制终止该进程组", newcwd
        if rc != 0 and not err.strip():
            err = f"(退出码 {rc})"
        return out, err, newcwd
    except Exception as e:
        return "", str(e), SESSIONS.get(sid, HOME_DIR)
    finally:
        if cg:
            _cgroup_del(cg)


def _pin_ip(host, port, scheme):
    """解析域名并返回要连接的 IP 字面量。

    防 DNS 重绑定：上一步 getaddrinfo 校验过所有记录都是公网 IP，
    这里再解析一次，取第一个结果。两次解析窗口极小，且上一步已经
    把内网 IP 全部拒掉了，攻击者要利用得在毫秒级内翻转 DNS，
    实际不可行 —— 但比完全不校验好。
    HTTPS 时要带 SNI，所以优先取 IPv4（兼容性最好）。
    """
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    if not infos:
        raise ValueError("无法解析 " + host)
    if scheme == "https":
        for fam, *_ , sa in infos:
            if fam == socket.AF_INET:
                return sa[0]
    return infos[0][4][0]

ENTRIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".entries.json")
_entries_lock = threading.Lock()


def entries_read():
    """读守护维护的入口注册表（含实测存活标记）。"""
    with _entries_lock:
        try:
            with open(ENTRIES_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except Exception:
            return []


def entry_best():
    """当前推荐给用户的入口：优先固定域名，其次最近还活着的。

    没有自有域名时只能用 Quick Tunnel，域名随机分配、进程重启就换，
    手机里存的链接会静默失效。守护把所有分配过的地址都记下来并逐个实测，
    这里只从"确认还活着"的里面挑 —— 宁可少给一个地址，
    也不能给一个点开是 1033 的地址。
    """
    lst = [e for e in entries_read() if e.get("alive")]
    for kind in ("named", "quick"):
        same = [e for e in lst if e.get("kind") == kind]
        if same:
            return max(same, key=lambda x: x.get("last_seen", "")).get("url")
    return None



class H(http.server.SimpleHTTPRequestHandler):
    # 音频/视频扩展名补齐（Python 内置表缺 webm/m4a，语音消息与视频回放依赖正确 MIME）
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".webm": "video/webm",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".mov": "video/quicktime",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }

    def _where_page(self, best, lst, alive_n):
        """/where 在手机浏览器里打开时返回的极简页面。

        为什么要有这个：Quick Tunnel 换域名后，你手机上存的旧链接会变成
        1033。修的办法很简单 —— 打开任意一个旧地址加 /where，
        但如果返回一堆 JSON 字段，你还得自己从里面找 url 那一行。
        所以浏览器直接访问时给一页"人话"：当前地址 + 一键复制。
        """
        import html as _h
        if best:
            head = ('<div style="color:#6ee7a0;font-size:15px;margin-bottom:6px">'
                    '● 当前可用</div>')
            main = ('<div id="u" style="font:600 15px/1.5 ui-monospace,monospace;'
                    'word-break:break-all;background:#111827;padding:14px;'
                    'border-radius:10px;margin:10px 0">%s</div>' % _h.escape(best))
        else:
            head = ('<div style="color:#ff8a80;font-size:15px;margin-bottom:6px">'
                    '● 暂无存活入口（隧道可能已断）</div>')
            main = ('<div style="color:#9ca3af;font-size:14px;line-height:1.7">'
                    '守护进程正在重连，稍等一两分钟再刷新这个页面。<br>'
                    '如果一直不行，把这个地址告诉我。</div>')
        rows = "".join(
            '<div style="padding:9px 0;border-bottom:1px solid #1f2937;'
            'font:13px/1.6 ui-monospace,monospace">'
            '<span style="color:%s">%s</span> '
            '<span style="opacity:.5;font-size:11px">%s</span><br>'
            '<span style="word-break:break-all;opacity:.85">%s</span></div>'
            % ("#6ee7a0" if e.get("alive") else "#ff8a80",
               "● 活" if e.get("alive") else "○ 死",
               _h.escape(str(e.get("kind") or "")),
               _h.escape(str(e.get("url") or "")))
            for e in reversed(lst))
        page = (
            '<!DOCTYPE html><html lang=zh><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>当前地址</title><style>'
            '*{box-sizing:border-box}'
            'body{margin:0;padding:22px 18px;background:#0b0a12;color:#e8e6ef;'
            'font:15px/1.6 system-ui,-apple-system,sans-serif;min-height:100vh}'
            'h1{font-size:19px;margin:0 0 4px}'
            '.tip{color:#9ca3af;font-size:13px;margin:0 0 18px}'
            'button{width:100%;padding:13px;border:0;border-radius:11px;'
            'background:linear-gradient(135deg,#7c6cf0,#5a4bd6);color:#fff;'
            'font-size:15px;font-weight:600;cursor:pointer;margin-top:6px}'
            '.h2{font-size:13px;color:#9ca3af;margin:24px 0 4px}'
            '.ft{color:#6b7280;font-size:11px;margin-top:20px;line-height:1.7}'
            '</style></head><body>'
            '<h1>Ubuntu 模拟器 · 当前地址</h1>'
            '<p class=tip>域名由 Cloudflare 随机分配，隧道重启就会换。'
            '这个页面永远告诉你现在该用哪个。</p>'
            + head + main
            + ('<button id=c>复制地址</button>'
               '<script>document.getElementById("c").onclick=function(){'
               'var t=document.getElementById("u").textContent;'
               'if(navigator.clipboard&&navigator.clipboard.writeText){'
               'navigator.clipboard.writeText(t);this.textContent="已复制 ✓";'
               '}else{var s=document.createRange();'
               's.selectNodeContents(document.getElementById("u"));'
               'document.execCommand("copy");this.textContent="已复制 ✓";}'
               'var b=this;setTimeout(function(){b.textContent="复制地址";},1800);};'
               '</script>' if best else '')
            + '<div class=h2>历史入口（只有"● 活"的能打开）</div>' + rows
            # 这里绝对不能印口令。/where 是免鉴权的，任何拿到地址的人
            # 都能打开这个页面 —— 之前把口令明文写在这里等于把钥匙挂在门口，
            # 别人打开 /where 就能直接进桌面。现在只提示口令由本人保管。
            + '<div class=ft>需要口令才能进入（口令只在你自己的消息里）　·　服务器时间 '
            + _h.escape(time.strftime("%Y-%m-%d %H:%M:%S"))
            + '<br>零信任隧道换域名是 Cloudflare 的规则，绕不过去。'
              '要永久固定只能买自己的域名。</div>'
            '</body></html>'
        )
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _cors(self):
        """同源部署：前端页面与 API 在同一个源，浏览器本身就允许请求，
        不需要任何 CORS 响应头。所以这里什么都不发 ——
        绝不能发 Access-Control-Allow-Origin: *，否则任何第三方网页都能
        用你已登录的浏览器调这个后端（CSRF + 把沙箱内容读回去）。"""
        if not SEND_CORS:
            return
        origin = self.headers.get("Origin", "")
        if origin and origin in ALLOW_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _unauth(self, why="未授权"):
        """未授权统一出口：API 回 JSON，页面请求回口令页。"""
        audit(self, "DENY", f"path={self.path} {why}")
        if self.path.startswith("/api/"):
            self._json({"err": f"{why}：缺少或无效的访问口令"}, 401)
            return
        # 页面路由（/ 与 /index.html）无条件跳登录页。别只靠 Accept 判断：
        # 某些客户端（curl 默认、某些 App WebView、脚本）不发 text/html，
        # 结果用户看到的是一段裸 JSON 报错而不是登录框，更难排查。
        is_page = urlparse(self.path).path in ("/", "/index.html")
        want_html = is_page or "text/html" in (self.headers.get("Accept", "") or "")
        if want_html:
            # 302 让浏览器自动跳到口令页；API 请求则回 JSON 401
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json({"err": f"{why}"}, 401)

    def _send_text(self, code, text):
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _proxy(self, target):
        try:
            p = urlparse(target)
            if p.scheme not in ("http", "https"):
                self._send_text(400, "只支持 http/https 链接")
                return
            if p.username or p.password:
                self._send_text(400, "不支持带账号密码的链接")
                return
            try:
                port = p.port or (443 if p.scheme == "https" else 80)
            except Exception:
                self._send_text(400, "端口不合法")
                return
            host = (p.hostname or "").lower()
            # 云元数据地址：即使 DNS 正常也直接拒
            if host in ("localhost", "0.0.0.0", "::1", ""):
                self._send_text(403, "出于安全限制，无法访问本地/内网地址")
                return
            if host in ("metadata.google.internal", "metadata.goog", "instance-data",
                        "169.254.169.254", "100.100.100.200"):
                self._send_text(403, "出于安全限制，无法访问云元数据服务")
                return
            # 解析全部 A/AAAA 记录并逐一校验：
            # 只查 gethostbyname 的第一个结果会被 DNS 重绑定绕过 ——
            # 攻击者让第一次解析返回公网 IP 通过校验，连接时再返回内网 IP。
            try:
                infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            except Exception:
                self._send_text(502, "无法解析主机: " + host)
                return
            if not infos:
                self._send_text(502, "无法解析主机: " + host)
                return
            for fam, _, _, _, sa in infos:
                ip = sa[0]
                try:
                    a = ipaddress.ip_address(ip)
                except Exception:
                    self._send_text(403, "地址不合法")
                    return
                if (a.is_private or a.is_loopback or a.is_link_local or
                        a.is_reserved or a.is_multicast or a.is_unspecified):
                    self._send_text(403, "出于安全限制，无法访问内网/本地地址 (" + ip + ")")
                    return
            # 校验通过后防 DNS 重绑定：
            # HTTP 可以把连接钉到已校验的 IP；但 HTTPS 不能 —— 用 IP 直连会
            # 证书主机名不匹配（TLS 校验会失败）。所以 HTTPS 保留域名交给
            # TLS 校验兜底，同时仍然拦掉 Cloudflare 等可信来源。
            if p.scheme == "https":
                real_url = target
            else:
                pinned = _pin_ip(host, port, p.scheme)
                if ":" in pinned:
                    real_url = p._replace(netloc="[%s]:%d" % (pinned, port)).geturl()
                else:
                    real_url = p._replace(netloc="%s:%d" % (pinned, port)).geturl()
            req = urllib.request.Request(real_url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
                "Accept": "*/*",
                "Host": host if port in (80, 443) else "%s:%d" % (host, port),
                "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                ctype = resp.headers.get("Content-Type", "") or ""
                data = resp.read(6_000_000)
            if "text/html" in ctype:
                html = data.decode("utf-8", "replace")
                html = _rewrite_html(html, target)
                b = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self._cors()
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self._cors()
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self._send_text(e.code, "目标页面返回错误 " + str(e.code))
        except Exception as e:
            self._send_text(502, "代理请求失败: " + str(e))


    def _json(self, obj, code=200, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        if cookie:
            # 页面导航（location.replace）只会带 Cookie，不会带 Authorization 头
            # ——那是 fetch 包装才加的。所以登录成功必须同时下发 Cookie，
            # 否则首页判定未认证 → 302 回 /login → 死循环。
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}={cookie}; Path=/; Max-Age={SESSION_TTL}; "
                             f"Secure; HttpOnly; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- 应用商店：真实下载到后端 + 单用户隔离 ----------------
    # 反馈 FB20261017220811AAB3：应用商店里的应用要能"真实下载到后端"，
    # 而且单个用户的下载/安装只影响他自己（单次请求仅对当前用户生效，和其他人没事）。
    # 做法：后端有一份目录 catalog.json；用户点"安装"时，后端把镜像真的写到
    # 该用户专属目录 store/users/<uid>/<id>/；前端再把这个用户装好的系统显示在他的
    # 虚拟机启动器里（给前台）。所有状态都按 uid 隔离，互不串台。
    def _store_uid(self):
        tok = _token_from_request(self)
        if not tok:
            return None
        if tok == "master":
            return "master"
        return "u_" + hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16]

    def _store_user_dir(self, uid):
        d = os.path.join(STORE_DIR, "users", uid)
        os.makedirs(d, exist_ok=True)
        return d

    def _store_manifest(self, uid):
        mp = os.path.join(self._store_user_dir(uid), "manifest.json")
        if not os.path.exists(mp):
            return {}
        try:
            with open(mp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _store_save_manifest(self, uid, m):
        mp = os.path.join(self._store_user_dir(uid), "manifest.json")
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(tmp, mp)

    def _store_load_catalog(self):
        if not os.path.exists(STORE_CATALOG):
            self._store_seed_catalog()
        try:
            with open(STORE_CATALOG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"items": []}

    def _store_seed_catalog(self):
        """首次写入默认目录：真实可下载的系统镜像 / 工具。
        后端会真的把它们下到各用户专属目录（不是前端假装的"已安装"）。"""
        cat = {"updated": "2026-10-17", "items": [
            {"id": "dsl", "name": "Damn Small Linux 2024", "icon": "🐧",
             "category": "系统镜像", "size": "50 MB",
             "desc": "极轻量 Linux 发行版，图形桌面 + 浏览器，约 50MB。",
             "src": "/win/images/dsl-4.11.rc2.iso", "fname": "dsl-4.11.rc2.iso",
             "sys": {"name": "Damn Small Linux", "mode": "cdrom", "slot": "cdrom",
                     "mem": 256, "img": "__STORE__"}},
            {"id": "tinycore", "name": "TinyCore Linux 11.0", "icon": "🐧",
             "category": "系统镜像", "size": "19 MB",
             "desc": "极简 Linux（busybox），最适合练命令行。",
             "src": "/win/images/TinyCore-11.0.iso", "fname": "TinyCore-11.0.iso",
             "sys": {"name": "TinyCore Linux 11.0", "mode": "cdrom", "slot": "cdrom",
                     "mem": 128, "img": "__STORE__"}},
            {"id": "kolibri", "name": "KolibriOS", "icon": "💾",
             "category": "系统镜像", "size": "1.4 MB",
             "desc": "俄罗斯的 1.4MB 微型图形系统，加载最快。",
             "src": "/win/images/kolibri.img", "fname": "kolibri.img",
             "sys": {"name": "KolibriOS", "mode": "image", "slot": "fda",
                     "mem": 32, "img": "__STORE__"}},
            {"id": "freedos", "name": "FreeDOS 1.3", "icon": "🖥️",
             "category": "系统镜像", "size": "720 KB",
             "desc": "开源 DOS，命令行练手神器。",
             "src": "/win/images/freedos722.img", "fname": "freedos722.img",
             "sys": {"name": "FreeDOS 1.3", "mode": "image", "slot": "fda",
                     "mem": 32, "img": "__STORE__"}},
            {"id": "msdos", "name": "MS-DOS 7.10 中文版", "icon": "🖥️",
             "category": "系统镜像", "size": "1.4 MB",
             "desc": "带中文支持的 MS-DOS 7.1（Win98 底层内核）。",
             "src": "/win/images/msdos.img", "fname": "msdos.img",
             "sys": {"name": "MS-DOS 7.10 中文版", "mode": "image", "slot": "fda",
                     "mem": 32, "img": "__STORE__"}},
            {"id": "windows101", "name": "Windows 1.01 (1985)", "icon": "🪟",
             "category": "系统镜像", "size": "1.4 MB",
             "desc": "微软第一版 Windows！40 年前的真品。",
             "src": "/win/images/windows101.img", "fname": "windows101.img",
             "sys": {"name": "Windows 1.01 (1985)", "mode": "image", "slot": "fda",
                     "mem": 64, "img": "__STORE__"}},
            {"id": "openbsd", "name": "OpenBSD 引导盘", "icon": "🐡",
             "category": "系统镜像", "size": "1.4 MB",
             "desc": "OpenBSD boot 引导器，启动到 boot> 提示符。",
             "src": "/win/images/openbsd.img", "fname": "openbsd.img",
             "sys": {"name": "OpenBSD 引导盘", "mode": "image", "slot": "fda",
                     "mem": 64, "img": "__STORE__"}},
            {"id": "solaros", "name": "Solar_OS 0.104", "icon": "☀️",
             "category": "系统镜像", "size": "1.4 MB",
             "desc": "罗马尼亚作者手搓的图形 hobby 系统。",
             "src": "/win/images/os8.dsk", "fname": "os8.dsk",
             "sys": {"name": "Solar_OS 0.104", "mode": "image", "slot": "fda",
                     "mem": 64, "img": "__STORE__"}},
            {"id": "slitaz", "name": "SliTaz 5.0-RC4", "icon": "🐧",
             "category": "系统镜像", "size": "56 MB",
             "desc": "SliTaz 滚动版，图形桌面 + 完整包管理器。",
             "src": "/win/images/slitaz-rolling-2024.iso", "fname": "slitaz-rolling-2024.iso",
             "sys": {"name": "SliTaz 5.0-RC4", "mode": "cdrom", "slot": "cdrom",
                     "mem": 384, "img": "__STORE__"}},
            # Win10/11 标注：v86 仅支持 32 位 x86 固件，跑不了 64 位 Windows 10/11。
            # 反馈 FB20261017221117A838「更新 win10 11」的诚实答复：
            # 不伪造镜像，而是明确约束并引导到「导入镜像」上传兼容镜像。
            {"id": "win10", "name": "Windows 10", "icon": "🪟", "category": "系统镜像",
             "size": "—", "unsupported": True,
             "desc": "v86 仅支持 32 位 x86 固件，无法启动 64 位 Windows 10/11。"
                     "可改用「导入镜像」上传兼容镜像，或关注后续 32 位系统扩充。"},
            {"id": "win11", "name": "Windows 11", "icon": "🪟", "category": "系统镜像",
             "size": "—", "unsupported": True,
             "desc": "v86 仅支持 32 位 x86 固件，无法启动 64 位 Windows 10/11。"
                     "可改用「导入镜像」上传兼容镜像，或关注后续 32 位系统扩充。"},
            # ---- 软件类：真实可运行的网页应用（非镜像）。----
            # 应用商店「软件」分类的主体。下载时后端不落盘镜像文件，
            # 仅在用户 manifest 标记已装；前端据 appKey 把对应 APP_REGISTRY
            # 应用并入桌面（需下载才出现，符合应用商店语义）。
            {"id": "app-firefox", "name": "Firefox 浏览器", "icon": "🌐",
             "category": "软件", "type": "software", "appKey": "Firefox",
             "size": "前端应用", "desc": "网页浏览器，支持多标签页与书签。"},
            {"id": "app-editor", "name": "文本编辑器", "icon": "📝",
             "category": "软件", "type": "software", "appKey": "文本编辑器",
             "size": "前端应用", "desc": "轻量文本编辑，支持查找与保存。"},
            {"id": "app-terminal", "name": "终端", "icon": "⌨️",
             "category": "软件", "type": "software", "appKey": "终端",
             "size": "前端应用", "desc": "命令行终端，练习 shell 与系统命令。"},
            {"id": "app-calc", "name": "计算器", "icon": "🧮",
             "category": "软件", "type": "software", "appKey": "计算器",
             "size": "前端应用", "desc": "科学计算器，支持四则运算与函数。"},
            {"id": "app-monitor", "name": "系统监视器", "icon": "📊",
             "category": "软件", "type": "software", "appKey": "系统监视器",
             "size": "前端应用", "desc": "查看 CPU、内存与进程占用。"},
            {"id": "app-shot", "name": "截图", "icon": "📸",
             "category": "软件", "type": "software", "appKey": "截图",
             "size": "前端应用", "desc": "区域截图并保存到图片。"},
            {"id": "app-image", "name": "图像查看器", "icon": "🖼️",
             "category": "软件", "type": "software", "appKey": "图像查看器",
             "size": "前端应用", "desc": "浏览本地图片，支持缩放。"},
            {"id": "app-disk", "name": "磁盘", "icon": "💽",
             "category": "软件", "type": "software", "appKey": "磁盘",
             "size": "前端应用", "desc": "磁盘与文件管理工具。"},
            {"id": "app-diskuse", "name": "磁盘使用分析器", "icon": "📉",
             "category": "软件", "type": "software", "appKey": "磁盘使用分析器",
             "size": "前端应用", "desc": "分析各目录空间占用。"},
            {"id": "app-calendar", "name": "日历", "icon": "📅",
             "category": "软件", "type": "software", "appKey": "日历",
             "size": "前端应用", "desc": "月历视图，查看日期与日程。"},
            {"id": "app-weather", "name": "天气", "icon": "⛅",
             "category": "软件", "type": "software", "appKey": "天气",
             "size": "前端应用", "desc": "查看当前城市天气。"},
            {"id": "app-help", "name": "帮助", "icon": "❓",
             "category": "软件", "type": "software", "appKey": "帮助",
             "size": "前端应用", "desc": "使用指南与常见问题。"},
        ]}
        try:
            with open(STORE_CATALOG, "w", encoding="utf-8") as f:
                json.dump(cat, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _store_download(self, uid, item):
        """把 item 的 src 真正写到该用户专属目录（真实下载到后端，非前端假装）。"""
        dest = os.path.join(self._store_user_dir(uid), item["id"])
        os.makedirs(dest, exist_ok=True)
        fp = os.path.join(dest, item.get("fname") or (item["id"] + ".img"))
        src = item.get("src", "")
        try:
            if src.startswith("http://") or src.startswith("https://"):
                req = urllib.request.Request(src)
                with urllib.request.urlopen(req, timeout=600) as resp, open(fp, "wb") as out:
                    while True:
                        buf = resp.read(1 << 20)
                        if not buf:
                            break
                        out.write(buf)
            else:
                local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     src.lstrip("/"))
                if not os.path.isfile(local):
                    return False, "源文件不存在: " + src
                with open(local, "rb") as fin, open(fp, "wb") as out:
                    while True:
                        buf = fin.read(1 << 20)
                        if not buf:
                            break
                        out.write(buf)
            return True, fp
        except Exception as e:
            return False, str(e)

    def do_POST(self):
        record_online_ip(self)
        # ---- IP 封禁闸门 ----
        # 必须放在所有处理之前：封禁期间一律拒绝，不能只是"不让登录"
        # 而其他 API 还能照打，那封禁形同虚设。
        _ip = _client_ip(self)
        _banned, _bt = ban_check(_ip)
        if _banned:
            audit(self, "BAN_BLOCK", f"ip={_ip} path={self.path}")
            self._json({"err": f"你的 IP 因多次尝试口令已被封禁，请 {_bt} 秒后再试"}, 429)
            return
        # ---- 安全闸门：所有 POST 都是特权操作（执行命令 / 写文件 / 装包）----
        # 限流桶的判定要等 body 读完才知道（exec 得看命令内容才能分快慢），
        # 所以这里只先把通用桶的检查做掉，exec 那一档挪到读完 body 之后。
        if self.path != "/api/exec":
            bucket = ("install" if self.path == "/api/install" else
                      "login" if self.path == "/api/login" else "general")
            ok, wait = _rate_ok(self, bucket)
            if not ok:
                audit(self, "RATELIMIT", f"path={self.path} bucket={bucket}")
                self._json({"err": f"请求过于频繁，请 {wait} 秒后再试（限流保护）"}, 429)
                return

        try:
            ln = int(self.headers.get("Content-Length", 0))
        except Exception:
            ln = 0
        if ln > 40 * 1024 * 1024:      # 上传上限，防止把内存/磁盘打满
            self._json({"err": "请求体过大（上限 40MB）"}, 413)
            return
        raw = self.rfile.read(ln) if ln else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        # exec 单独分档：慢命令（装包/编译/下载）如果和 ls 共用一个配额，
        # 跑两条 install 就把这一分钟的次数用光，之后连 ls 都发不出去。
        if self.path == "/api/exec":
            _is_slow = exec_timeout_for(data.get("cmd", ""))[1]
            bucket = "exec_slow" if _is_slow else "exec"
            ok, wait = _rate_ok(self, bucket)
            if not ok:
                audit(self, "RATELIMIT",
                      f"path={self.path} bucket={bucket} cmd={data.get('cmd','')[:40]!r}")
                self._json({"err": f"请求过于频繁，请 {wait} 秒后再试（限流保护）"}, 429)
                return

        # ---- CSRF 防护 ----
        # Bearer 头本身已能挡住 CSRF（跨站页面无法读取 token，也无法
        # 自定义 Authorization 头 —— 简单请求预检会失败）。这里是第二道：
        # 校验 Origin/Referer 必须是自己这个源，防止将来有人图省事把
        # token 改回 Cookie 传递时立刻被打脸。
        if self.path != "/api/login":
            ref = self.headers.get("Origin") or self.headers.get("Referer") or ""
            if ref:
                host_hdr = self.headers.get("Host", "")
                try:
                    rh = urlparse(ref).netloc
                except Exception:
                    rh = ""
                if rh and host_hdr and rh != host_hdr:
                    self._json({"err": "跨站请求被拒绝（CSRF 防护）"}, 403)
                    return

        if self.path == "/api/login":
            # 注意：global 必须在读这些名字之前声明，否则 SyntaxError
            global LOGIN_FAIL_GLOBAL, LOGIN_LOCK_UNTIL, _fb_lock_level
            # 全局爆破闸门：按 IP 限流挡不住轮换 IP，必须全站一起算账
            if time.time() < LOGIN_LOCK_UNTIL:
                wait = int(LOGIN_LOCK_UNTIL - time.time()) + 1
                audit(self, "LOGIN_LOCKED", f"还有 {wait}s")
                self._json({"ok": False, "err": f"口令错误次数过多，请 {wait} 秒后再试"}, 429)
                return
            # 拿口令换 token。前端首屏口令框调这个，成功后存 sessionStorage。
            given = str(data.get("key", ""))
            ok_key, which = key_allowed(given, _client_ip(self))
            if not ok_key and which and "主密钥" in str(which):
                # 口令本身是对的，只是 IP 不在白名单 —— 必须分开报，
                # 不然用户会以为口令错了，反复重试进不来
                audit(self, "PRIMARY_IP_BLOCK", f"ip={_client_ip(self)} 拒绝：{which}")
                self._json({"ok": False, "err": str(which)}, 403)
                return
            if not ok_key:
                # 记下来：谁在试口令、试了多少次。爆破靠这个能看出苗头。
                with _fb_lock:
                    LOGIN_FAIL_GLOBAL += 1
                    if LOGIN_FAIL_GLOBAL >= LOGIN_FAIL_MAX:
                        _fb_lock_level = min(_fb_lock_level + 1, 6)
                        wait = LOGIN_BACKOFF_BASE * (2 ** (_fb_lock_level - 1))
                        LOGIN_LOCK_UNTIL = time.time() + wait
                        LOGIN_FAIL_GLOBAL = 0
                        audit(self, "BRUTE_LOCK", f"全站锁定 {wait}s（连续失败 {LOGIN_FAIL_MAX} 次）")
                        self._json({"ok": False,
                                    "err": f"口令错误次数过多，全站锁定 {wait} 秒"},
                                   429)
                        return
                audit(self, "LOGIN_FAIL", f'guess="{given[:12]}"')
                # 1 秒内试口令超过 5 次 → 直接封这个 IP
                banned, ttl = ban_hit(_client_ip(self))
                if banned:
                    audit(self, "BAN",
                          f"ip={_client_ip(self)} 1秒内试口令 >{BAN_MAX_PER_SEC} 次，封 {ttl}s")
                    self._json({"ok": False,
                                "err": f"尝试过于频繁，本 IP 已被封禁 {ttl // 60} 分钟"},
                               429)
                    return
                self._json({"ok": False, "err": "口令不对"}, 401)
                return
            # 登录成功：清零失败计数与锁定
            with _fb_lock:
                LOGIN_FAIL_GLOBAL = 0
                _fb_lock_level = 0
                LOGIN_LOCK_UNTIL = 0.0
            tok = secrets.token_urlsafe(24)
            session_put(tok)
            # 必须同时下发 Cookie：页面跳转请求不会带 Authorization 头
            # 记清命中的是哪个密钥：主密钥还是对外的测试密钥。
            # 这样"我的密钥被谁用了"和"测试密钥被谁用了"能分开回答。
            audit(self, "LOGIN_OK", f"密钥={which} session 已建立")
            self._json({"ok": True, "token": tok, "ttl": SESSION_TTL}, cookie=tok)
            return

        # ---- 应急解锁：给当前 IP 授权主密钥 ----
        # 移动网络 IP 会变，用户换地方后主密钥就用不了了。
        # 用备用密钥进来调这个，把当前 IP 加进白名单即可 —— 不会被锁死。
        #
        # 关键约束：备用密钥只能授权"自己当前所在的 IP"，不能指定任意 IP。
        # 否则就等于用测试密钥给全世界的 IP 开后门，主密钥的 IP 限制形同虚设
        # —— 攻击者只要知道测试密钥，就能把自己的 IP 加进白名单。
        if self.path == "/api/trust":
            given = str(data.get("key", ""))
            ok_tr, which_tr = _key_match(given)
            if not ok_tr:
                audit(self, "TRUST_DENY", "口令不对")
                banned, ttl = ban_hit(_client_ip(self))
                if banned:
                    audit(self, "BAN",
                          f"ip={_client_ip(self)} 1秒内试口令 >{BAN_MAX_PER_SEC} 次，封 {ttl}s")
                    self._json({"ok": False,
                                "err": f"尝试过于频繁，本 IP 已被封禁 {ttl // 60} 分钟"},
                               429)
                    return
                self._json({"ok": False, "err": "口令不对"}, 401)
                return
            me = _client_ip(self)
            if which_tr == "主密钥":
                # 已经是主密钥了，直接把当前 IP 加白名单
                added, n = ip_allow_add(me)
                audit(self, "TRUST", f"主密钥为当前 IP 授权 ip={me} 共{n}条")
                self._json({"ok": True, "added": added, "count": n,
                            "ip": me, "ips": ip_allow_list(),
                            "msg": f"已把 {me} 加入主密钥白名单"})
                return
            act = str(data.get("add", "") and "add" or data.get("action", "add"))
            # 不管什么操作，只能作用于自己这个 IP
            if act == "list":
                self._json({"ok": True, "ips": ip_allow_list(), "me": me})
                return
            if act == "del":
                ok_d, n = ip_allow_del(me)
                audit(self, "TRUST_DEL", f"ip={me} {'已删除' if ok_d else '不存在'}")
                self._json({"ok": ok_d, "count": n, "ips": ip_allow_list()})
                return
            added, n = ip_allow_add(me)
            audit(self, "TRUST",
                  f"把 ip={me} 加入主密钥白名单（{'新增' if added else '已存在'}），共 {n} 条")
            self._json({"ok": True, "added": added, "count": n, "ip": me,
                        "ips": ip_allow_list(),
                        "msg": (f"已授权 {me}，"
                                f"主密钥现在可在 {n} 个 IP 上使用")})
            return

        # 问题反馈：允许匿名提交（公开收集用户建议），仅做 IP 限流，
        # 直接跳过主鉴权门——避免“重启/会话失效后登录态丢失导致无法提交”。
        # 检查更新：只读版本号，同样免鉴权（旧会话失效也要能检查更新）。
        if self.path == "/api/feedback":
            if not _fb_allowed(_client_ip(self)):
                self._json({"ok": False, "err": "提交太频繁，请稍后再试（限流保护）"})
                return
        elif self.path == "/api/version":
            self._json({"ok": True, "version": _server_version()})
            return
        elif self.path == "/api/feedback/query":
            if not _fb_allowed(_client_ip(self)):
                self._json({"ok": False, "err": "查询太频繁，请稍后再试"})
                return
            self._json(_fb_query(_client_ip(self)))
            return
        elif not _authed(self, data.get("key")):
            self._unauth()
            return
        elif self.path == "/api/vm64/start":
            return self._vm64_start(data)
        elif self.path == "/api/vm64/stop":
            return self._vm64_stop()
        elif self.path == "/api/vm64/status":
            return self._vm64_status()
        elif self.path == "/api/vm64/isos":
            return self._vm64_isos()
        elif self.path in ("/api/friend", "/api/dm/send", "/api/dm/msgs", "/api/dm/recall", "/api/room/recall"):
            if str(data.get("nick", "")): pass
            data["_uid"] = _uid_of(str(data.get("nick", ""))[:24] or "访客", self)
            if self.path == "/api/friend":
                return self._json(_friend_api(data, self))
            if self.path == "/api/dm/send":
                return self._json(_dm_send(data, self))
            if self.path == "/api/dm/recall":
                return self._json(_dm_recall(data, self))
            if self.path == "/api/room/recall":
                return self._json(_room_recall(data, self))
            return self._json(_dm_msgs(data, self))
        elif self.path == "/api/nick":
            return self._json(_nick_api(data, self))
        elif self.path == "/api/room/send":
            return self._json(_room_send(data, self))
        elif self.path == "/api/room/msgs":
            return self._json(_room_msgs(data))
        elif self.path == "/api/room/recall":
            return self._json(_room_recall(data, self))
        elif self.path == "/api/dm/recall":
            return self._json(_dm_recall(data, self))
        elif self.path == "/api/call":
            return self._json(_call_api(data, self))
        elif self.path == "/api/reg":
            return self._json(_reg_api(data))

        if self.path == "/api/exec":
            sid = str(data.get("sid") or "default")
            cwd = data.get("cwd")
            cmd = data.get("cmd", "")
            # 超时按命令特征自动选档，并把实际值回给前端。
            # 前端必须比这个数再晚一点放弃，否则会出现"命令其实在跑、
            # 页面却报无法连接后端"的假象（前端 20s < 后端 30s，踩过一次）
            to, slow = exec_timeout_for(cmd)
            out, err, newcwd = user_bash(cmd, cwd, sid, timeout=to)
            if len(out) > 8000: out = out[-8000:] + "\n... (输出已截断)"
            if len(err) > 2000: err = err[-2000:]
            self._json({"out": out, "err": err, "cwd": newcwd,
                        "timeout": to, "slow": slow})
        elif self.path == "/api/feedback":
            # 桌面「问题反馈」应用：写一行 JSON 到 feedback/feedback.jsonl
            # 我（维护方）定期读取这个文件，按建议修，修完在「更新记录」里写明
            ftype = str(data.get("type", "其他"))[:20] or "其他"
            title = str(data.get("title", "")).strip()[:60]
            detail = str(data.get("detail", "")).strip()[:2000]
            contact = str(data.get("contact", "")).strip()[:60]
            url = str(data.get("url", ""))[:300]
            ua = str(data.get("ua", ""))[:300]
            # 版本门槛：用户在「检查更新」里选择"取消更新"继续用旧版本时，
            # 旧版本提交的反馈不会被记录（客户端 ver 与服务端不一致直接丢弃）。
            # 不带 ver 的旧客户端无法判断，照旧记录（避免误杀）。
            _cv = str(data.get("ver", "")).strip()
            _sv = _server_version()
            if _cv and _sv and _cv != _sv:
                audit(self, "FEEDBACK_STALE", f"ver={_cv} (server={_sv}) 已丢弃")
                self._json({"ok": True, "stale": True,
                            "msg": "旧版本反馈不记录"})
                return
            if not title:
                self._json({"ok": False, "err": "标题不能为空"})
                return
            if len(detail) < 5:
                self._json({"ok": False, "err": "详细描述太短（至少 5 个字）"})
                return
            import random as _rnd
            fid = "FB" + time.strftime("%Y%m%d%H%M%S") + \
                  "".join(_rnd.choice("0123456789ABCDEF") for _ in range(4))
            rec = {
                "id": fid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ip": _client_ip(self), "type": ftype,
                "title": title, "detail": detail,
                "contact": contact, "url": url, "ua": ua,
                "fixed": False,
            }
            fb_dir = os.path.join(_here, "feedback")
            os.makedirs(fb_dir, exist_ok=True)
            fb_file = os.path.join(fb_dir, "feedback.jsonl")
            try:
                # 追加写 + flock，防止并发写串行
                with open(fb_file, "a", encoding="utf-8") as fh:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    except Exception:
                        pass
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as e:
                self._json({"ok": False, "err": f"写入失败：{e}"})
                return
            audit(self, "FEEDBACK", f"id={fid} type={ftype} title={title[:30]!r}")
            reg_event("FEEDBACK", ftype + "：" + title[:50])
            self._json({"ok": True, "id": fid})
        elif self.path == "/api/upload":
            # dir=images 时落 win/images/（v86 镜像目录），供"导入镜像"用；
            # 带 append+offset 时分片追加写，支持几 GB 的大 Windows 镜像
            # （整文件 base64 会炸内存，分片后每片 8MB 轻松过）。
            files = data if isinstance(data, list) else [data]
            dest_dir = USER_DIR
            if isinstance(data, dict) and data.get("dir") == "images":
                dest_dir = os.path.join(_here, "win", "images")
                os.makedirs(dest_dir, exist_ok=True)
            elif isinstance(data, dict) and data.get("dir") == "room":
                dest_dir = os.path.join(_here, "win", "room_files")
                os.makedirs(dest_dir, exist_ok=True)
            saved = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                name = os.path.basename(str(f.get("name", "file")))
                b64 = f.get("data", "")
                try:
                    rawf = base64.b64decode(b64)
                except Exception:
                    rawf = b""
                # 单片上限 32MB（分片模式下单片就是 8MB，永远不超）
                if len(rawf) > 32 * 1024 * 1024:
                    continue
                if rawf:
                    fp = os.path.join(dest_dir, name)
                    if isinstance(data, dict) and data.get("append") \
                            and data.get("offset") is not None:
                        # 分片追加：按 offset 定位写入，断点续传靠客户端重传该片
                        with open(fp, "r+b" if os.path.exists(fp) else "wb") as fh:
                            fh.seek(int(data["offset"]))
                            fh.write(rawf)
                    else:
                        with open(fp, "wb") as fh:
                            fh.write(rawf)
                    saved.append(name)
            self._json({"ok": True, "saved": saved, "dir": dest_dir})
        elif self.path == "/api/write":
            fn = os.path.basename(str(data.get("name", "")))
            content = data.get("content", "")
            if not fn:
                self._json({"ok": False, "err": "缺少文件名"})
            else:
                with open(os.path.join(USER_DIR, fn), "w", encoding="utf-8") as fh:
                    fh.write(content)
                self._json({"ok": True, "name": fn, "dir": USER_DIR})
        elif self.path == "/api/install":
            out, err = install_packages(data.get("pkgs", ""))
            self._json({"out": out, "err": err})
        elif self.path == "/api/store/install":
            # 应用商店：把应用（系统镜像）真实下载到当前用户的专属目录。
            uid = self._store_uid()
            if not uid:
                self._json({"err": "未登录"}, 401)
                return
            cid = str(data.get("id", ""))
            cat = self._store_load_catalog()
            item = next((x for x in cat.get("items", []) if x.get("id") == cid), None)
            if not item:
                self._json({"err": "目录里没有这个应用"}, 404)
                return
            if item.get("unsupported"):
                self._json({"err": item.get("desc", "该应用暂不支持")}, 400)
                return
            # 软件类：前端 app 代码本就存在，不落盘镜像文件，仅在 manifest 标记已装
            if item.get("type") == "software":
                m = self._store_manifest(uid)
                items = m.get("items", {})
                items[cid] = {"installed_at": int(time.time()),
                              "type": "software", "appKey": item.get("appKey")}
                m["items"] = items
                self._store_save_manifest(uid, m)
                audit(self, "STORE_INSTALL", f"uid={uid} app={cid} (software)")
                self._json({"ok": True, "id": cid, "type": "software",
                            "appKey": item.get("appKey")})
                return
            ok, res = self._store_download(uid, item)
            if not ok:
                self._json({"err": "下载失败: " + str(res)}, 500)
                return
            m = self._store_manifest(uid)
            items = m.get("items", {})
            items[cid] = {"installed_at": int(time.time()),
                          "fname": item.get("fname") or (cid + ".img"),
                          "size": os.path.getsize(res)}
            m["items"] = items
            self._store_save_manifest(uid, m)
            audit(self, "STORE_INSTALL", f"uid={uid} app={cid}")
            self._json({"ok": True, "id": cid, "path": res,
                        "size": os.path.getsize(res)})
        elif self.path == "/api/store/uninstall":
            uid = self._store_uid()
            if not uid:
                self._json({"err": "未登录"}, 401)
                return
            cid = str(data.get("id", ""))
            m = self._store_manifest(uid)
            items = m.get("items", {})
            if cid not in items:
                self._json({"ok": True, "removed": False})
                return
            # 只删这个用户自己的目录，不影响其他用户（单用户隔离）
            d = os.path.join(self._store_user_dir(uid), cid)
            import shutil
            try:
                if os.path.isdir(d):
                    shutil.rmtree(d)
            except Exception:
                pass
            del items[cid]
            m["items"] = items
            self._store_save_manifest(uid, m)
            audit(self, "STORE_UNINSTALL", f"uid={uid} app={cid}")
            self._json({"ok": True, "removed": True})
        else:
            self._json({"err": "not found"}, 404)

    def _serve_file(self, rel, cacheable=False, ctype=None):
        """从项目根目录读一个文件发出去（不经过 SimpleHTTPRequestHandler 的路径解析）。"""
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(os.path.join(base, rel.lstrip("/")))
        if not path.startswith(base + os.sep) or not os.path.isfile(path):
            self._json({"err": "not found"}, 404)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            self._json({"err": str(e)}, 500)
            return
        ct = ctype or ("text/html; charset=utf-8" if path.endswith((".html", ".htm"))
                       else "application/javascript; charset=utf-8" if path.endswith(".js")
                       else "text/css; charset=utf-8" if path.endswith(".css")
                       else "application/octet-stream")
        self._cacheable = cacheable
        self.send_response(200)
        self.send_header("Content-Type", ct)
        # 注入"公网地址已变更"横幅。
        # 为什么要在这里注入而不是让前端去查接口：Quick Tunnel 的域名是
        # 随机分配的，隧道一重启就换。你手机里存的旧链接会静默失效 ——
        # 而后端一切正常，没有任何报错，是最难自己发现的故障。
        # 所以把新地址直接顶到页面最上面，一进来就看得见。
        if ctype is None and path.endswith((".html", ".htm")) \
                and rel.lstrip("/") in ("index.html", "login.html"):
            data = self._inject_url_notice(data)
            data = self._inject_entry_bar(data)
        self.send_header("Content-Length", str(len(data)))
        if cacheable:
            # 可缓存资源（v86 镜像等大文件）单独发，end_headers 里的禁缓存
            # 对它不生效（_cacheable=True 会跳过）
            self.send_header("Cache-Control", "public, max-age=86400")
        self._cors()
        self.end_headers()
        self._cacheable = False
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _inject_url_notice(self, data):
        """在 <body> 后插入一个"网址已变更"横幅（只在守护写了通知时才插）。"""
        np = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".url_notice.json")
        try:
            if not os.path.exists(np):
                return data
            with open(np, "r", encoding="utf-8") as f:
                d = json.load(f)
            new, old, at = d.get("new", ""), d.get("old", ""), d.get("at", "")
            if not new:
                return data
        except Exception:
            return data
        # 已经自己关闭掉了就别再显示
        if self.headers.get("X-URL-Notice-Close") == "1":
            return data
        import html as _h
        banner = (
            # pointer-events:none —— 横幅是 fixed 盖在最上层，桌面上是 fixed 全屏，
            # 占位 div 顶不动它，首行图标会被整条盖住点不到。放开指针事件，
            # 只让"知道了"按钮可点，下方内容照常可交互。
            '<div id="urlNotice" style="position:fixed;left:0;right:0;top:0;z-index:99999;'
            'pointer-events:none;'
            'background:#b3261e;color:#fff;font:14px/1.5 system-ui,sans-serif;'
            'padding:10px 12px;box-shadow:0 2px 12px rgba(0,0,0,.4)">'
            '<b>公网地址已变更</b>（隧道重启导致，你手机里存的旧链接已失效）<br>'
            '<span style="font-family:ui-monospace,monospace;font-size:13px;'
            'word-break:break-all">' + _h.escape(new) + '</span><br>'
            '<span style="opacity:.85;font-size:12px">变更时间 ' + _h.escape(at) +
            '　旧地址 ' + _h.escape(old or "-") + '</span>'
            # 点"知道了"要连占位 div 一起删掉，否则那 88/112px 的空白
            # 会一直留着，矮屏上登录框被顶到需要滚动才看得见。
            # 注意用 addEventListener 而非内联 onclick：横幅本身已经
            # 在 body 里注入过脚本，内联再套一层容易出转义问题。
            '<button id="urlNoticeClose" '
            'style="float:right;background:#fff;color:#b3261e;border:0;'
            'pointer-events:auto;'
            'border-radius:4px;padding:4px 10px;font-weight:600">知道了</button>'
            '<div style="clear:both"></div></div>'
            # 横幅是 fixed:top:0，会盖住正文。撑一个占位元素把内容顶下去，
            # 否则用户第一眼看到的全是横幅，输入框要往下滑才够得着。
            '<div id="urlNoticeGap" style="height:%dpx"></div>'
            '<script>(function(){var b=document.getElementById("urlNoticeClose");'
            'if(b)b.onclick=function(){'
            'var n=document.getElementById("urlNotice"),'
            'g=document.getElementById("urlNoticeGap");'
            'if(n)n.remove();if(g)g.remove();};})();</script>'
            % (112 if len(new) > 46 else 88)
        )
        # 同 _inject_entry_bar：拼出来的 HTML 必须标签平衡才敢注入。
        # 漏一个 </div> 就会把整页内容吞进横幅容器，页面直接空白。
        n_open = len(re.findall(r"<div\b", banner))
        n_close = len(re.findall(r"</div>", banner))
        if n_open != n_close:
            print("[warn] 横幅标签不平衡(%d开/%d闭)，本次跳过注入"
                  % (n_open, n_close), flush=True)
            return data
        try:
            html = data.decode("utf-8")
        except Exception:
            return data
        for tag in ("<body>", "<body >", "<BODY>"):
            if tag in html:
                return html.replace(tag, tag + banner, 1).encode("utf-8")
        # 没找到 <body> 就塞在 <head> 之后，至少别丢
        return html.replace("</head>", "</head>" + banner, 1).encode("utf-8")

    def _inject_entry_bar(self, data):
        """在页面底部插一条常驻的"当前入口"状态条。

        为什么要常驻而不是只在出错时显示：Quick Tunnel 换域名时，
        你手机上存的旧链接会变成 1033，而页面上什么都看不到（Cloudflare
        的错误页）。有条子在这儿，至少你一眼能看到"地址已经变了"。
        折叠状态默认隐藏，点一下展开全部历史入口。
        """
        best = entry_best()
        lst = entries_read()
        if not lst:
            return data
        import html as _h
        alive_n = sum(1 for e in lst if e.get("alive"))
        rows = "".join(
            '<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,.08)">'
            '<span style="color:%s">%s</span> '
            '<span style="opacity:.55;font-size:10px">[%s]</span> '
            '<span style="font-family:ui-monospace,monospace;font-size:10px;'
            'word-break:break-all;opacity:.8">%s</span></div>'
            % ("#6ee7a0" if e.get("alive") else "#ff8a80",
               "● 活着" if e.get("alive") else "○ 已失效",
               _h.escape(str(e.get("kind") or "")),
               _h.escape(str(e.get("url") or "")))
            for e in reversed(lst))
        # 入口圆点。用"零尺寸 fixed 容器 + 绝对定位子元素"这一招。
        # 踩过的坑：直接给 fixed 元素写 right/bottom，在登录页上无效 ——
        # login.html 的 body 是 display:grid，Chromium 会把这个 fixed
        # 后代按 grid item 参与尺寸计算，容器被拉成 400x524，圆点被顶到
        # 屏幕上方（top=-155），用户根本点不到，而且完全看不出原因。
        # 容器固定 0x0（grid 拉不动它），子元素再用 absolute 定位到
        # 右下角，位置就与外部布局彻底解耦。
        panel = (
            '<div id="entryBar" style="position:fixed;right:0;bottom:0;left:auto;'
            'top:auto;width:0;height:0;overflow:visible;z-index:99998">'
            '<div id="entryBarBody" style="display:none;position:absolute;'
            'right:6px;bottom:34px;width:min(86vw,320px);max-height:56vh;overflow:auto;'
            'background:rgba(18,18,22,.97);border:1px solid rgba(255,255,255,.16);'
            'border-radius:10px;padding:8px 10px;box-shadow:0 10px 34px rgba(0,0,0,.6);'
            'color:#eee;font:12px/1.5 system-ui,-apple-system,sans-serif">'
            '<div style="font-weight:600;margin-bottom:5px;'
            'font-family:ui-monospace,monospace;font-size:11px;color:#6ee7a0">'
            '当前入口 ' + str(alive_n) + '/' + str(len(lst)) + ' 可用</div>'
            + rows +
            '<div style="padding:7px 0 0;opacity:.6;font-size:10px">'
            '域名由 Cloudflare 随机分配，隧道重启就会变。上面是历史入口，'
            '只有"● 活着"的可访问。完整列表：<code>/where</code></div></div>'
            '<div id="entryBarHead" title="入口状态" style="position:absolute;'
            'right:6px;bottom:6px;width:26px;height:26px;line-height:24px;'
            'text-align:center;border-radius:50%;background:rgba(18,18,22,.88);'
            'border:1px solid rgba(255,255,255,.22);cursor:pointer;font-size:13px;'
            'opacity:.5;box-shadow:0 2px 10px rgba(0,0,0,.4)">⇅</div>'
            '<script>(function(){var h=document.getElementById("entryBarHead"),'
            'b=document.getElementById("entryBarBody");'
            'if(h&&b){h.onclick=function(e){e.stopPropagation();'
            'b.style.display=b.style.display==="none"?"block":"none";};'
            'document.addEventListener("click",function(){b.style.display="none";});'
            'b.addEventListener("click",function(e){e.stopPropagation();});'
            '}})();</script>'
            # 这个 </div> 闭合 #entryBar 外层容器，千万别漏。
            # 漏了的后果极其隐蔽：浏览器会把整个页面后续内容（登录框、
            # 脚本）全吞进这个 0x0 容器里，页面看起来"什么都没有"，
            # 而 HTML 是合法的、CSS 也全对，只能靠数标签平衡才查得出来。
            # 下面紧跟的标签平衡自检就是为它准备的。
            '</div>'
        )
        # ---- 标签平衡自检（这道防线救过命）----
        # 之前漏了一个 </div>，浏览器就把整个页面吞进 0x0 的容器里：
        # 页面全空白、HTTP 200、CSS 全部正确，人和日志都看不出问题。
        # 字符串拼 HTML 天然容易漏标签，所以在注入前先数一遍，
        # 不平衡就宁可不注入（少个圆点无所谓，页面全废是灾难）。
        n_open = len(re.findall(r"<div\b", panel))
        n_close = len(re.findall(r"</div>", panel))
        if n_open != n_close:
            print("[warn] 入口条标签不平衡(%d开/%d闭)，本次跳过注入"
                  % (n_open, n_close), flush=True)
            return data
        try:
            html = data.decode("utf-8")
        except Exception:
            return data
        for tag in ("<body>", "<body >", "<BODY>"):
            if tag in html:
                return html.replace(tag, tag + panel, 1).encode("utf-8")
        return html.replace("</head>", "</head>" + panel, 1).encode("utf-8")

    def do_GET(self):
        try:
            if _force_https(self): return
        except Exception:
            pass
        record_online_ip(self)
        # /api/ban?key=xxx —— 查自己的封禁状态 / 列出当前封禁
        # 用主密钥或备用密钥都能查。解封用 /api/ban/del。
        if self.path.split("?")[0] == "/api/ban":
            kq0 = parse_qs(urlparse(self.path).query)
            k0 = (kq0.get("key") or [""])[0]
            if not _key_match(k0)[0]:
                self._json({"err": "口令不对"}, 401)
                return
            me0 = _client_ip(self)
            b0, w0 = ban_check(me0)
            with _ban_lock:
                now0 = time.time()
                alive0 = [{"ip": i, "remain": int(v - now0), "hits": _BAN_HITS.get(i, 1)}
                          for i, v in _BANS.items() if v > now0]
            self._json({"ok": True, "banned": b0, "remain": w0,
                        "ip": me0, "bans": alive0,
                        "rule": f"1秒内试口令 >{BAN_MAX_PER_SEC} 次即封 {BAN_TTL // 60} 分钟",
                        "exempt": sorted(BAN_EXEMPT)[:20],
                        "exempt_count": len(BAN_EXEMPT)})
            return
        # IP 封禁对 GET 同样生效：不然封了之后镜像还能下载、页面还能翻，
        # 封禁只挡一半等于没挡。/api/ban 自测接口放行，方便你查状态。
        if not self.path.startswith("/api/ban"):
            _ipg = _client_ip(self)
            _bg, _btg = ban_check(_ipg)
            if _bg:
                audit(self, "BAN_BLOCK", f"ip={_ipg} path={self.path}")
                if self.path.startswith("/api/"):
                    self._json({"err": f"你的 IP 因多次尝试口令已被封禁，"
                                        f"请 {_btg} 秒后再试"}, 429)
                else:
                    self._send_text(429, f"你的 IP 因多次尝试口令已被封禁，请 {_btg} 秒后再试")
                return
        kq = parse_qs(urlparse(self.path).query)
        k = (kq.get("key") or [""])[0]

        # 口令页不需要认证
        if self.path == "/login":
            # 但已经带着有效会话的话没必要再输一遍，直接送回桌面。
            # 不做这一步的话，会话失效/换设备时用户会反复撞这个页面。
            if _authed(self, k or None):
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._serve_file("login.html", cacheable=False)

        # ---- /emergency.html ----
        # 应急备用页：无后端也能运行的静态页，必须免登录公开可下载
        # （它的用途就是主站故障时从 GitHub Pages 拿，以及给 Pages 直传当源文件）。
        if self.path == "/emergency.html":
            return self._serve_file("emergency.html", cacheable=True)

        # ---- /favicon.ico ----
        # 单独处理而不是靠 404 兜底：手机浏览器打开任何页面都会自动
        # 请求一次 favicon，走 404 会在控制台留红色报错、审计日志留
        # DENY 记录，看起来像出了故障。返回 204 干净且最省流量。
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return

        # ---- 域名归属验证文件（微信/百度/Google 等）免鉴权公开 ----
        # 验证方爬虫不带会话直接 GET 该 <hex>.txt，若走认证会 302 到登录页
        # 导致验证失败。仅放行"全 hex 文件名"类验证文件，绝不暴露内部 *.txt
        # （如上线IP日志.txt / 安全审计汇总.txt 等），根目录须存在同名文件。
        _vf = re.match(r"^/([0-9a-f]{32})\.txt$", self.path or "")
        if _vf:
            _vfp = os.path.join(_here, _vf.group(1) + ".txt")
            if os.path.isfile(_vfp):
                with open(_vfp, "r", encoding="utf-8") as _f:
                    _vdata = _f.read()
                _vb = _vdata.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(_vb)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_vb)
            else:
                self._send_text(404, "验证文件不存在")
            return

        # ---- /where —— 告诉我现在哪个地址是活的（免鉴权）----
        # 存在的意义：Quick Tunnel 域名随机，重启就换。你手机里存的旧链接
        # 会变成 1033，而这个接口能让你（或我）一条命令问出当前该用哪个。
        # 只返回域名，不泄露任何密钥；唯一"代价"是暴露入口地址，
        # 而地址本来就在浏览器地址栏里，不算额外泄露。
        if self.path == "/where":
            best = entry_best()
            lst = entries_read()
            alive_n = sum(1 for e in lst if e.get("alive"))
            # 手机浏览器打开的应该是"人话"，不是一堆 JSON 字段。
            # 加 ?page 参数（或直接浏览器访问时带 Accept: text/html）
            # 就返回一个只有地址 + 一键复制按钮的极简页面。
            ua = (self.headers.get("User-Agent") or "").lower()
            want_page = ("page=1" in (self.path or "")
                         or "text/html" in (self.headers.get("Accept") or ""))
            if want_page and "mozilla" in ua:
                self._where_page(best, lst, alive_n)
                return
            self._json({"ok": True, "entry": best,
                        "alive_count": alive_n, "total": len(lst),
                        "all": [{"url": e.get("url"), "kind": e.get("kind"),
                                 "alive": bool(e.get("alive")),
                                 "first": e.get("first_seen"),
                                 "last": e.get("last_seen")}
                                for e in lst],
                        "server_time": time.strftime("%Y-%m-%d %H:%M:%S")})
            return

        # ---- ?key=口令 → 换 HttpOnly Cookie 后跳回首页（方便直接贴链接）----
        if k and key_allowed(k, _client_ip(self))[0]:
            tok = secrets.token_urlsafe(24)
            session_put(tok)
            audit(self, "LOGIN_OK", f"密钥={_key_match(k)[1]} 来源=URL ?key= 链接")
            self.send_response(302)
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}={tok}; Path=/; Max-Age={SESSION_TTL}; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path.startswith("/api/"):
            # 只读 API 也保护：/api/proxy 能读任意公网页面，/api/logs 能读系统日志
            ok, wait = _rate_ok(self, "general")
            if not ok:
                self._json({"err": f"请求过于频繁，请 {wait} 秒后再试（限流保护）"}, 429)
                return
            if not _authed(self, (kq.get("key") or [None])[0]):
                self._unauth()
                return
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/api/identity":
                sid = q.get("sid", ["default"])[0]
                out, _, cwd = user_bash("whoami; hostname; echo $HOME", None, sid)
                lines = [x for x in out.strip().splitlines() if x.strip()]
                self._json({"user": lines[0] if len(lines) > 0 else USER_ACCOUNT,
                            "host": lines[1] if len(lines) > 1 else "ubuntu",
                            "home": lines[2] if len(lines) > 2 else HOME_DIR,
                            "cwd": cwd})
            elif u.path == "/api/ps":
                out, _ = run_bash("ps -eo pid,comm,user,%cpu,rss --sort=-%cpu --no-headers 2>/dev/null | head -15")
                rows = []
                for line in out.strip().split("\n"):
                    p = line.split()
                    if len(p) >= 5:
                        try:
                            rows.append({"pid": int(p[0]), "cmd": p[1], "user": p[2],
                                         "cpu": float(p[3]), "mem": f"{int(p[4])/1024:.1f} MiB"})
                        except: pass
                self._json({"rows": rows})
            elif u.path == "/api/logs":
                t = q.get("type", ["dmesg"])[0]
                if t == "audit":
                    # 安全审计：谁登录成功/失败、谁在试提权、谁被限流
                    kind = q.get("kind", [""])[0]
                    n = min(int(q.get("n", ["40"])[0]), 200)
                    rows = audit_query(self, kind, n)
                    self._json({"rows": rows})
                    return
                c = {"dmesg": "dmesg 2>/dev/null | tail -50",
                     "journal": "journalctl -n 50 --no-pager 2>/dev/null",
                     "auth": "journalctl -n 30 --no-pager 2>/dev/null | grep -i auth"}.get(t, "echo 无")
                out, err = run_bash(c)
                self._json({"out": (out or err or "(无日志 / 无权限)")[-4000:]})
            elif u.path == "/api/disks":
                out, _ = run_bash("df -h / /tmp 2>/dev/null")
                rows = []
                for line in out.strip().split("\n")[1:]:
                    p = line.split()
                    if len(p) >= 6:
                        rows.append({"fs": p[0], "size": p[1], "used": p[2], "avail": p[3], "mount": p[5]})
                self._json({"rows": rows})
            elif u.path == "/api/proxy":
                tgt = q.get("url", [""])[0]
                if not tgt:
                    self._send_text(400, "缺少 url 参数")
                else:
                    self._proxy(tgt)
            elif u.path == "/api/list":
                try:
                    items = []
                    for fn in sorted(os.listdir(USER_DIR)):
                        fp = os.path.join(USER_DIR, fn)
                        if os.path.isfile(fp):
                            items.append({"name": fn, "size": os.path.getsize(fp), "mtime": int(os.path.getmtime(fp))})
                    self._json({"items": items, "dir": USER_DIR})
                except Exception as e:
                    self._json({"items": [], "err": str(e)})
            elif u.path == "/api/images":
                # v86 镜像目录清单（boot.html 菜单和"虚拟机"窗口导入功能共用）
                try:
                    items = []
                    imgdir = os.path.join(_here, "win", "images")
                    for fn in sorted(os.listdir(imgdir)):
                        fp = os.path.join(imgdir, fn)
                        if os.path.isfile(fp):
                            items.append({"name": fn, "size": os.path.getsize(fp)})
                    self._json({"items": items})
                except Exception as e:
                    self._json({"items": [], "err": str(e)})
            elif u.path.startswith("/api/store/"):
                # ---- 应用商店接口 ----
                if u.path == "/api/store/catalog":
                    uid = self._store_uid()
                    if not uid:
                        self._json({"err": "未登录"}, 401)
                        return
                    cat = self._store_load_catalog()
                    m = self._store_manifest(uid)
                    owned = set((m or {}).get("items", {}).keys())
                    out = []
                    for it in cat.get("items", []):
                        it = dict(it)
                        it["installed"] = it["id"] in owned
                        out.append(it)
                    self._json({"items": out, "updated": cat.get("updated")})
                elif u.path == "/api/store/mine":
                    uid = self._store_uid()
                    if not uid:
                        self._json({"err": "未登录"}, 401)
                        return
                    cat = self._store_load_catalog()
                    m = self._store_manifest(uid)
                    mine = (m or {}).get("items", {})
                    installed = []
                    for it in cat.get("items", []):
                        if it["id"] in mine and not it.get("unsupported"):
                            installed.append({"id": it["id"], "name": it["name"],
                                             "icon": it.get("icon"), "sys": it.get("sys"),
                                             "type": it.get("type", "image"),
                                             "appKey": it.get("appKey"),
                                             "installed_at": mine[it["id"]].get("installed_at")})
                    self._json({"items": installed})
                elif u.path.startswith("/api/store/file"):
                    # 把当前用户装好的镜像发给前端（带鉴权），前端转成 blob 交给 v86。
                    # 只服务该用户自己装的文件，天然实现单用户隔离。
                    uid = self._store_uid()
                    if not uid:
                        self._send_text(401, "未登录")
                        return
                    qid = q.get("id", [""])[0]
                    m = self._store_manifest(uid)
                    if qid not in (m or {}).get("items", {}):
                        self._send_text(404, "未安装")
                        return
                    cat = self._store_load_catalog()
                    item = next((x for x in cat.get("items", []) if x["id"] == qid), None)
                    fname = (item or {}).get("fname") or (qid + ".img")
                    fp = os.path.join(self._store_user_dir(uid), qid, fname)
                    if not os.path.isfile(fp):
                        self._send_text(404, "文件缺失")
                        return
                    self._serve_image(fp)
                else:
                    self._json({"err": "unknown store endpoint"}, 404)
            elif u.path == "/api/file":
                fn = q.get("name", [""])[0]
                fp = os.path.join(USER_DIR, os.path.basename(fn))
                if not fn or not os.path.isfile(fp):
                    self._send_text(404, "文件不存在")
                else:
                    with open(fp, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self._cors()
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
            else:
                self._json({"err": "unknown"}, 404)
        elif self.path.startswith("/win/images/") or self.path.startswith("/win/boot/"):
            # v86 磁盘/光盘镜像与引导文件：支持 Range 断点续传 + 长缓存
            # （否则每次都要重下几十 MB，手机上根本等不起）
            name = os.path.basename(unquote(urlparse(self.path).path))
            sub = "images" if self.path.startswith("/win/images/") else "boot"
            fp = os.path.join(_here, "win", sub, name)
            if not name or not os.path.isfile(fp):
                self._send_text(404, "镜像不存在")
            else:
                self._serve_image(fp)
        else:
            # ---- 其余页面/静态资源也要认证 ----
            # 否则任何人拿到 URL 就能直接读 script.js / apps.js 的源码，
            # 也能在没口令时把整个模拟器前端扒下来。
            p = urlparse(self.path).path
            if p in ("/", "/index.html"):
                if not _authed(self, (kq.get("key") or [None])[0]):
                    return self._unauth()
                return self._serve_file("index.html", cacheable=False)
            if not _authed(self, (kq.get("key") or [None])[0]):
                return self._unauth()
            super().do_GET()

    def _serve_image(self, fs_path):
        """带 Range 支持的镜像流式返回，v86 边下边开机就靠它。"""
        try:
            size = os.path.getsize(fs_path)
        except OSError:
            return self._send_text(404, "镜像不存在")
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "") or "")
        if m:
            s, e = m.group(1), m.group(2)
            if s == "" and e:
                start = max(0, size - int(e))
            elif s:
                start = int(s)
                if e:
                    end = min(int(e), size - 1)
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            code = 206
        length = end - start + 1
        self._cacheable = True
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        with open(fs_path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def end_headers(self):
        if not getattr(self, "_cacheable", False):
            # 页面/脚本/API 一律禁缓存。之前只发 no-store，部分手机浏览器
            # 仍会启发式缓存，服务端改了半天用户看到的还是旧版本。
            # 三件套一起发，兼容性最好。
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        # 基本安全响应头
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        # HSTS：本站全程 HTTPS（Cloudflare 隧道），强制浏览器后续只走 HTTPS，
        # 防降级 / 中间人。Quick Tunnel 域名会变，故不加 includeSubDomains
        # ——对随机主机名没意义，还可能误伤同域其他服务。
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        # Permissions-Policy：关掉本应用完全用不到的浏览器能力，收窄攻击面。
        # 注意**绝不能**关 clipboard —— 页面"一键复制地址"按钮依赖它。
        # microphone / geolocation 必须放行（聊天室语音消息与位置共享依赖，之前误封导致"不支持录音/定位"）。
        self.send_header("Permissions-Policy",
                         "camera=(self), microphone=(self), geolocation=(self), "
                         "payment=(), usb=(), magnetometer=(), gyroscope=(), "
                         "accelerometer=(), midi=()")
        # 页面要允许 iframe 载入真实 GNOME 桌面（同一域名），但禁止第三方嵌入。
        # frame-src 单列：default-src 会兜底 frame-src 导致外域 iframe 全被拦。
        # 放行 mc.js 官方站与官方客户端线路（「Minecraft 网页版」窗口内直达）。
        self.send_header("Content-Security-Policy",
                         "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: "
                         "http://localhost:8080; frame-src 'self' https://mcjs.link "
                         "https://*.mcjs.cc https://mcjs.144449.xyz https://vm64.3920850391.dpdns.org https://mcjs-beta.144449.xyz "
                         "https://playmcjscc.pages.dev; frame-ancestors 'self'")
        super().end_headers()


    # ---- 64 位虚拟机（服务端 QEMU + noVNC 显示；wasm 做不了 64 位，真机可以）----
    def _vm64_procs(self, fresh=False):
        running, novnc, pid = _vm64_cache_read(fresh=fresh)
        return [str(pid)] if pid else []

    def _vm64_status(self):
        running, novnc, _ = _vm64_cache_read()
        self._json({"ok": True, "running": running, "novnc": novnc})

    def _vm64_start(self, data):
        try:
            mem = min(max(int(data.get("mem_gb", 4)), 1), 32)
            cores = min(max(int(data.get("cores", 2)), 1), 16)
            disk_gb = min(max(int(data.get("disk_gb", 0)), 0), 100)
        except Exception:
            return self._json({"ok": False, "err": "参数不对"})
        iso = str(data.get("iso", ""))
        iso_path = os.path.join(_here, "win", "images", iso)
        if not iso or "/" in iso or ".." in iso or not os.path.isfile(iso_path):
            return self._json({"ok": False, "err": "ISO 不存在：先在应用商店把系统镜像下载到服务器"})
        if self._vm64_procs(fresh=True):
            return self._json({"ok": True, "msg": "64位虚拟机已在运行"})
        vm_dir = os.path.join(_here, "win", "vm64")
        os.makedirs(vm_dir, exist_ok=True)
        disk = None
        if disk_gb:
            disk = os.path.join(vm_dir, "disk.qcow2")
            if not os.path.exists(disk) or os.path.getsize(disk) < 4096:
                subprocess.run(["qemu-img", "create", "-f", "qcow2", disk, f"{disk_gb}G"], timeout=60)
        cmd = ["qemu-system-x86_64", "-accel", "tcg,thread=multi", "-cpu", "max",
               "-smp", str(cores), "-m", f"{mem}G",
               "-cdrom", iso_path, "-boot", "d",
               "-usb", "-device", "usb-tablet",
               "-vnc", "127.0.0.1:2", "-daemonize", "-name", "vm64"]
        if disk:
            cmd += ["-drive", f"file={disk},format=qcow2,if=virtio"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            err = (r.stderr or "")[-200:]
            if "Cannot allocate memory" in err:
                err = "服务器内存配额不足（容器上限 8GB，含系统占用，虚拟机内存当前最高 4GB）"
            return self._json({"ok": False, "err": "QEMU 启动失败: " + err})
        _, novnc, _ = _vm64_cache_read(fresh=True)
        if not novnc:
            subprocess.Popen(["websockify", "--heartbeat", "30", "--web", "/usr/share/novnc", "0.0.0.0:8081", "127.0.0.1:5902"],
                             start_new_session=True,
                             stdout=open("/tmp/ws64.log", "a"), stderr=subprocess.STDOUT)
        audit(self, "VM64_START", f"mem={mem}G cores={cores} disk={disk_gb}G iso={iso}")
        reg_event("VM64_START", f"{mem}G/{cores}核/{iso}")
        self._json({"ok": True, "host": "vm64.3920850391.dpdns.org"})

    def _vm64_isos(self):
        d = os.path.join(_here, "win", "images")
        out = []
        try:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".iso"):
                    try:
                        out.append({"name": f, "size_mb": round(os.path.getsize(os.path.join(d, f))/1048576)})
                    except Exception:
                        pass
        except Exception:
            pass
        self._json({"ok": True, "isos": out})

    def _vm64_stop(self):
        pids = self._vm64_procs(fresh=True)
        for p in pids:
            try:
                os.kill(int(p), 15)
            except Exception:
                pass
        audit(self, "VM64_STOP", ",".join(pids))
        reg_event("VM64_STOP", "已停止 %d 个实例" % len(pids))
        self._json({"ok": True, "stopped": len(pids)})

    def log_message(self, *a): pass

class BoundedThreadingServer(socketserver.ThreadingTCPServer):
    """多线程 + 两级配额隔离。

    为什么要分两级：原来是"总数 24 个线程"的单一上限。
    问题是 /api/exec 现在最长能跑 5 分钟（装包、编译），而手机浏览器
    每个页面要发好几个请求。如果 8 个慢命令同时挂着，就把 24 个名额占满，
    接着你点任何东西都是 503 —— 表现为"整个页面卡死"，
    而实际只是有人跑了条 sleep 300。
    浏览器要的是"秒回"，长任务要的是"能跑够久"，两者不能抢同一批名额。

    配额划分（20 台机器 126G 内存，跑几百个并发 bash 也没问题）：
      快通道 32 个 —— 页面、静态资源、状态查询。永远优先，绝不被慢任务饿死
      慢通道  8 个 —— 装包/编译这类长命令。满了就排队，而不是把站点拖垮
    两通道合计 40，比原来的 24 还宽裕，但互不干扰。
    """

    daemon_threads = True
    request_queue_size = 128
    max_threads = 32          # 快通道
    # 慢通道 16 个。上一版设 8，偏紧：慢命令现在能跑 5 分钟，
    # 8 个名额意味着 8 个人同时装包就满了，后面所有人（包括只想敲个
    # apt-cache search 的）全被拒。机器有 126G 内存和几十核，16 个
    # 并发 bash 完全撑得住，而且每个都被 cgroup 限了 2GB / 256 进程。
    max_slow = 16
    # 单 IP 慢任务上限：一个人不能把慢通道全占光。
    # 放宽到 3：你自己在终端里连开两个任务（一个装包一个看文档）是常态，
    # 2 个太紧，会出现"前一个还没跑完就发不出第二条"的烦人情况。
    per_ip_slow = 3

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._fast = threading.BoundedSemaphore(self.max_threads)
        self._slow = threading.BoundedSemaphore(self.max_slow)
        # ip -> 正在跑的慢任务数
        self._ip_slow = {}
        self._ip_lock = threading.Lock()
        # socket -> 已占用的通道。
        # 为什么必须用 dict 挂而不能写 self._kind：self 是 Server 实例，
        # 被所有连接共享。process_request 跑在主线程的同一个 self 上，
        # 并发时 A 请求刚写完 self._kind="fast"，B 就覆盖成 "slow"，
        # A 结束时按错的通道 release —— 信号量永久失衡，
        # 最后所有请求都被拒（配额越用越少，重启才恢复）。
        # socketserver 的 socket 对象可哈希，能直接当 key。
        self._taken = {}
        self._taken_lock = threading.Lock()

    def _peek(self, request):
        """偷看请求头而不消费它。MSG_PEEK 不从缓冲区取走数据，
        所以这里看归看，不影响后面 handler 正常解析。

        必须设超时：客户端连上 TCP 之后可能迟迟不发数据（扫描器、
        半开连接、手机切后台断网）。不设超时的话 MSG_PEEK 会一直阻塞在
        主线程的 process_request 里 —— 那是单线程的主循环，
        一个哑连接就能把整个服务器卡死。
        """
        try:
            # 超时从 5s 收到 0.5s：这段跑在 accept 主线程上，
            # 一旦有连接握手完但数据晚到（keep-alive 复用、半开连接、
            # 扫描器），主线程就会一直阻塞在这里，**所有新连接被迫排队**，
            # 表现为页面偶发卡顿整 5 秒、守护据此误判后端"半僵死"并重启。
            # 正常客户端请求行都在毫秒级到达，0.5s 足够；取不到就按快通道处理。
            request.settimeout(0.5)
            data = request.recv(4096, socket.MSG_PEEK)
            request.settimeout(None)     # 交还控制权，让 handler 自己定超时
            return data
        except Exception:
            return b""

    def _is_slow_request(self, line):
        """这个请求该不该进慢通道。

        判定必须在开线程之前完成 —— 线程开出来再判断，配额已经发出去了。

        关键：不能只看路径。原来写成"只要是 POST /api/exec 就算慢"，
        结果 echo / ls / pwd 这些 1 毫秒就完事儿的命令也被丢进慢通道。
        慢通道一共 8 个名额，一被人用满，你连 ls 都发不出去 ——
        而慢命令 5 分钟才跑完，等它跑完黄花菜都凉了。
        所以必须看命令内容，快的走快通道。
        """
        if not line:
            return False
        if b"POST /api/install" in line or b"/api/proxy" in line:
            return True
        if b"POST /api/exec" not in line:
            return False
        # 从请求体里抠出 cmd 字段。MSG_PEEK 通常已经能看到完整 POST
        # （浏览器一次性发完头和体），但也可能只有头 —— 那就退回按慢处理，
        # 宁可保守也不能把真慢命令误放快通道。
        m = re.search(rb'"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', line)
        if not m:
            try:
                cmd = json.loads(line.decode("utf-8", "replace"))["cmd"]
            except Exception:
                return True
        else:
            try:
                cmd = json.loads(b'"' + m.group(1) + b'"')
            except Exception:
                cmd = m.group(1).decode("utf-8", "replace")
        return exec_timeout_for(cmd)[1]

    def _peek_ip(self, line, client_address):
        """从请求头里偷看来源 IP。

        注意 client_address 必须当参数传进来：Server 实例上没有这个属性，
        它只存在于 handler 实例上。原来写成 self.client_address 会抛
        AttributeError，而且抛在 process_request 里 —— 每个慢请求都崩一次，
        表现为"命令瞬间返回空结果"，看起来像后端没生效，其实是崩了。
        """
        m = re.search(r"(?im)^CF-Connecting-IP:\s*([0-9a-f.:]+)",
                      line.decode("utf-8", "replace"))
        if m:
            return m.group(1)
        m = re.search(r"(?im)^X-Forwarded-For:\s*([0-9a-f.:]+)",
                      line.decode("utf-8", "replace"))
        if m:
            return m.group(1)
        try:
            return client_address[0] if client_address else "?"
        except Exception:
            return "?"

    def process_request(self, request, client_address):
        # 先看线程预算够不够，再去 peek。凑够配额再发现开不出线程是最糟的顺序
        try:
            if threading.active_count() >= self.max_threads + self.max_slow + 8:
                self._reject(request, 503,
                             "服务器并发已满（%d 个任务正在运行），请稍候重试"
                             % threading.active_count())
                return
        except Exception:
            pass
        # 只偷看一次：MSG_PEEK 之后数据仍在缓冲区，但每次 peek 都要付一次
        # 系统调用，而且 socket 超时状态会被反复改写。
        peeked = self._peek(request)
        if self._is_slow_request(peeked):
            ip = self._peek_ip(peeked, client_address)
            with self._ip_lock:
                if self._ip_slow.get(ip, 0) >= self.per_ip_slow:
                    self._reject(request, 429,
                                 "你同时运行的命令太多了（上限 %d 条），"
                                 "等前面的命令跑完再试" % self.per_ip_slow)
                    return
            if not self._slow.acquire(blocking=False):
                self._reject(request, 503,
                             "服务器正在处理其他耗时任务（最多 %d 条并发），请稍候重试"
                             % self.max_slow)
                return
            with self._ip_lock:
                self._ip_slow[ip] = self._ip_slow.get(ip, 0) + 1
            with self._taken_lock:
                self._taken[request] = ("slow", ip)
        else:
            got = self._fast.acquire(blocking=False)
            if not got:
                # 快通道满了也别硬拒。手机弱网重传很常见，
                # 等 8 秒比直接回 503 体验好得多。
                got = self._fast.acquire(timeout=8.0)
            if not got:
                self._reject(request, 503, "服务器繁忙，请稍候重试")
                return
            with self._taken_lock:
                self._taken[request] = ("fast", None)

        try:
            super().process_request(request, client_address)
        except RuntimeError as e:
            # 线程开不出来（地址空间耗尽 / 线程数超限）。
            # 必须自己接住：super() 抛的异常会让 socketserver 打印一屏
            # traceback 然后 shutdown_request，客户端收到的是连接重置 ——
            # 表现就是页面突然白屏/空白，比 503 糟糕得多。
            # 这里把已经占到的配额还回去，再回一个像样的 503。
            self._release(request)
            if "thread" in str(e).lower():
                glog = ("服务器已达到并发上限，请稍候重试"
                        "（如果连续出现，说明有命令卡住了）")
                self._reject(request, 503, glog)
            else:
                self._reject(request, 500, "服务器内部错误")
            return
        except Exception:
            self._release(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(request)

    def _release(self, request):
        with self._taken_lock:
            got = self._taken.pop(request, None)
        if not got:
            return
        kind, ip = got
        if kind == "fast":
            try:
                self._fast.release()
            except ValueError:
                pass          # 已超发，忽略而不是让守护线程崩掉
            return
        try:
            self._slow.release()
        except ValueError:
            pass
        with self._ip_lock:
            n = self._ip_slow.get(ip, 1) - 1
            if n > 0:
                self._ip_slow[ip] = n
            else:
                self._ip_slow.pop(ip, None)

    def _reject(self, request, code, msg):
        try:
            request.settimeout(5)
            body = msg.encode("utf-8")
            request.sendall(
                ("HTTP/1.1 %d %s\r\nContent-Type: text/plain; charset=utf-8\r\n"
                 "Content-Length: %d\r\nRetry-After: 5\r\n"
                 "Connection: close\r\n\r\n"
                 % (code, "Too Many Requests" if code == 429 else
                    "Service Unavailable", len(body))).encode()
                + body)
        except Exception:
            pass
        self.shutdown_request(request)


try:
    _reg_boot()
except Exception:
    pass
if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    BoundedThreadingServer.allow_reuse_address = True
    # 线程栈调小。默认 8MB/线程，32+16 个线程光栈就 380MB 地址空间，
    # 而 /api/exec 每条命令还会 fork 出 runuser+bash 两个进程。
    # 实测在 2GB RLIMIT_AS 下，16 条慢命令就能把地址空间吃穿，
    # 表现是 RuntimeError: can't start new thread —— 抛在主线程的
    # process_request 里，客户端直接收到连接重置（页面显示不出来）。
    # 1MB 对纯 Python 线程绰绰有余（bash 子进程是独立进程，不占这个）。
    try:
        threading.stack_size(1024 * 1024)
    except Exception:
        pass
    # 给自己设内存上限。/api/exec 是公网可达的命令执行入口，万一哪条命令
    # 又绕过 _drain 的限量把后端内存吃爆，没有上限的话整个机器会被拖垮
    # （连带 cloudflared 一起死，表现就是隧道 502）。
    #
    # 为什么从 2GB 提到 6GB：RLIMIT_AS 限制的是**虚拟地址空间**，
    # 不只是实际驻留内存。线程栈、glibc 的 malloc arena、mmap 的
    # 共享库映射全算在内。之前 2GB 时 48 个线程 + 几十个子进程就撑爆了
    # （can't start new thread），而这些进程的实际 RSS 加起来才 200MB。
    # 6GB 是"地址空间"意义上的余量，不是"允许用 6GB 内存"——
    # 真正的内存消耗另有 cgroup memory 2GB 兜底，两层是独立的。
    try:
        import resource
        _soft, _hard = resource.getrlimit(resource.RLIMIT_AS)
        _cap = 6 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS,
                           (_cap, _hard if _hard != resource.RLIM_INFINITY else _cap))
    except Exception as e:
        print(f"[warn] 设置内存上限失败: {e}", flush=True)
    # 只绑回环：cloudflared 与后端同机，公网流量必须经隧道进来，
    # 直接绑 0.0.0.0 会让沙箱内网和同宿主机的其他服务也能直接打到这里。
    with BoundedThreadingServer(("127.0.0.1", PORT), H) as httpd:
        print(f"cloudsystem 后端已启动 127.0.0.1:{PORT} "
              f"(多线程，上限 {httpd.max_threads} 线程)", flush=True)
        print(f"访问口令 (LINUXSIM_KEY): {ACCESS_KEY}", flush=True)
        print("把这个 key 拼到网址后面即可，例如 ?key=你的口令", flush=True)
        # 定期清掉过期会话 + 刷新自己的出口 IP 基准
        def _reap():
            while True:
                time.sleep(600)
                _refresh_self_ips()
                _refresh_ban_exempt()
                session_reap()
        threading.Thread(target=_reap, daemon=True).start()
        threading.Thread(target=_refresh_self_ips, daemon=True).start()
        def _exempt_loop():
            # 采集自己出口 IP 要几秒，第一次刷新时 _SELF_IPS 还是空的。
            # 头几分钟多刷几次，等采到再收手 —— 否则豁免名单一直是空的，
            # 手滑输错几次就会把自己封了。
            for _ in range(12):
                _refresh_self_ips()
                _refresh_ban_exempt()
                if BAN_EXEMPT:
                    break
                time.sleep(10)
        threading.Thread(target=_exempt_loop, daemon=True).start()
        # vm64 进程状态缓存：后台读 /proc 刷新，状态查询走缓存不再 fork pgrep
        threading.Thread(target=_vm64_cache_thread, daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            print("正在关闭…", flush=True)
            httpd.shutdown()

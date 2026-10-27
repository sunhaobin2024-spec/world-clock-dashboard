#!/usr/bin/env python3
# ============================================================
# 后端管理界面（最高权限） — system.3920850391.dpdns.org
# 监听 127.0.0.1:8443，仅经 Cloudflare Tunnel 对外暴露。
# 登录：systemd / 2015@3306Aa（会话 Cookie，登录限流，全程审计）
# 功能：主机/服务状态、反馈管理（标记修复/回复）、服务重启、日志查看
# ============================================================
import http.server, socketserver, json, subprocess, os, time, hmac, secrets, threading, shutil, fcntl, shlex, re

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8443
ADMIN_USER = "systemd"
ADMIN_PASS = "2015@3306Aa"
SESSION_TTL = 2 * 3600
AUDIT_LOG = os.path.join(HERE, "feedback", "admin_audit.log")

_sess_lock = threading.Lock()
SESSIONS = {}          # sid -> expiry
_exec_hits = {}
_fail_lock = threading.Lock()
FAILS = {}             # ip -> [ts,...]

# ---- 反馈文件写锁（与 server.py 共用 feedback.jsonl，flock 保护）----
def _fb_file():
    return os.path.join(HERE, "feedback", "feedback.jsonl")

def _audit(ip, act, detail=""):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "ip": ip, "act": act, "detail": detail},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass

def _login_allowed(ip):
    """登录限流：10 分钟内失败 >= 8 次锁定。"""
    now = time.time()
    with _fail_lock:
        hits = [t for t in FAILS.get(ip, []) if now - t < 600]
        FAILS[ip] = hits
        return len(hits) < 8

def _login_fail(ip):
    with _fail_lock:
        FAILS.setdefault(ip, []).append(time.time())

def _proc_running(kind):
    """按 /proc/cmdline 精确判定服务是否在跑。
    kind: "server.py" / "guardian.py" / "feedback_monitor.py" / "cloudflared"
    python 脚本按 argv[1] 以 /<脚本名> 结尾判定（不会误匹配 admin_server.py）。
    """
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = [a for a in open(f"/proc/{pid}/cmdline", "rb").read().decode().split("\0") if a]
        except Exception:
            continue
        if not argv:
            continue
        if kind == "cloudflared":
            if argv[0].endswith("cloudflared") and len(argv) > 1 and argv[1] == "tunnel":
                n += 1
        elif len(argv) >= 2 and "python" in os.path.basename(argv[0]) \
                and (argv[1] == kind or argv[1].endswith("/" + kind)):
            n += 1
    return n

def _host_status():
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            mem[k.strip()] = int(v.strip().split()[0])  # kB
    except Exception:
        pass
    load = ""
    try:
        load = open("/proc/loadavg").read().split()[:3]
        load = " / ".join(load)
    except Exception:
        pass
    up = ""
    try:
        up = open("/proc/uptime").read().split()[0]
        s = int(float(up)); up = f"{s//86400}天 {s%86400//3600}小时 {s%3600//60}分 {s%60}秒"
    except Exception:
        pass
    disk = {}
    try:
        u = shutil.disk_usage(HERE)
        disk = {"total_gb": round(u.total/2**30, 1), "used_gb": round(u.used/2**30, 1),
                "free_gb": round(u.free/2**30, 1), "pct": round(u.used/u.total*100, 1)}
    except Exception:
        pass
    # 站点版本
    ver = None
    try:
        import re
        m = re.search(r'window\.CURRENT_VERSION\s*=\s*"([^"]+)"',
                      open(os.path.join(HERE, "changelog.js"), encoding="utf-8").read())
        ver = m.group(1) if m else None
    except Exception:
        pass
    # 反馈统计
    fixed = pending = 0
    try:
        for line in open(_fb_file(), encoding="utf-8"):
            line = line.strip()
            if not line: continue
            r = json.loads(line)
            if r.get("fixed"): fixed += 1
            else: pending += 1
    except Exception:
        pass
    # 服务最近重启时间（hive 里 restart_<name> 键），直接并入状态，
    # 让状态页少发一趟 /api/reg 请求，降低隧道往返带来的卡顿。
    restarts = {}
    try:
        import sqlite3
        c = sqlite3.connect(os.path.join(HERE, "reg.db"), timeout=5)
        c.execute("CREATE TABLE IF NOT EXISTS hive(k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
        for k, ts in c.execute("SELECT k,ts FROM hive WHERE k LIKE 'restart_%'"):
            restarts[k[8:]] = ts
        c.close()
    except Exception:
        pass
    return {
        "mem": {"total_gb": round(mem.get("MemTotal", 0)/2**20, 1),
                "avail_gb": round(mem.get("MemAvailable", 0)/2**20, 1)},
        "load": load, "uptime": up, "disk": disk,
        "version": ver, "fb_fixed": fixed, "fb_pending": pending,
        "restarts": restarts,
        "services": {
            "server": _proc_running("server.py") > 0,
            "cloudflared": _proc_running("cloudflared") > 0,
            "guardian": _proc_running("guardian.py") > 0,
            "feedback_monitor": _proc_running("feedback_monitor.py") > 0,
            "admin": True,
        },
    }

def _fb_read_all():
    out = []
    try:
        for line in open(_fb_file(), encoding="utf-8"):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    except Exception:
        pass
    out.reverse()  # 新的在前
    return out

def _fb_action(data, ip):
    fid = str(data.get("id", ""))
    act = str(data.get("action", ""))
    reply = str(data.get("reply", "")).strip()[:1000]
    if not fid or act not in ("fix", "unfix", "reply"):
        return {"ok": False, "err": "参数不对"}
    src = _fb_file()
    tmp = src + ".tmp"
    changed = False
    try:
        with open(src, "r", encoding="utf-8") as fi, open(tmp, "w", encoding="utf-8") as fo:
            fcntl.flock(fo.fileno(), fcntl.LOCK_EX)
            for line in fi:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    fo.write(line + "\n"); continue
                if r.get("id") == fid:
                    changed = True
                    if act == "fix": r["fixed"] = True
                    elif act == "unfix": r["fixed"] = False
                    elif act == "reply" and reply: r["reply"] = reply
                    line = json.dumps(r, ensure_ascii=False)
                fo.write(line + "\n")
        os.replace(tmp, src)
    except Exception as e:
        return {"ok": False, "err": str(e)}
    _audit(ip, "FB_" + act.upper(), f"id={fid}")
    return {"ok": changed, "err": None if changed else "没找到该 ID"}

def reg_set_admin(k, v):
    """向共享注册表 reg.db 写键（与服务端 hive 共用）。"""
    import sqlite3
    try:
        c = sqlite3.connect(os.path.join(HERE, "reg.db"), timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS hive(k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
        c.execute("INSERT INTO hive(k,v,ts) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts",
                  (k, str(v), time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit(); c.close()
    except Exception:
        pass


def _kill_pids(kind):
    """按 /proc/cmdline 精确找 PID 并 SIGTERM（pkill 模式会漏杀相对路径进程）。"""
    pids = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = [a for a in open(f"/proc/{pid}/cmdline", "rb").read().decode().split(chr(0)) if a]
        except Exception:
            continue
        if not argv:
            continue
        if kind == "cloudflared":
            if argv[0].endswith("cloudflared") and len(argv) > 1 and argv[1] == "tunnel":
                pids.append(int(pid))
        elif len(argv) >= 2 and "python" in os.path.basename(argv[0]) and (argv[1] == kind or argv[1].endswith("/" + kind)):
            if "admin_server" not in argv[1]:
                pids.append(int(pid))
    for p in pids:
        try:
            os.kill(p, 15)
        except Exception:
            pass
    return pids


def _service_restart(name, ip):
    import urllib.request as _u
    targets = {"server": "server.py", "guardian": "guardian.py",
               "monitor": "feedback_monitor.py", "cloudflared": "cloudflared"}
    if name not in targets:
        return {"ok": False, "err": "未知服务"}
    kind = targets[name]
    killed = _kill_pids(kind)
    time.sleep(1.5)
    if name == "server":
        subprocess.Popen(["bash", "-c", "cd %s && nohup python3 server.py >/tmp/linuxsim_server.log 2>&1 &" % HERE],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif name == "guardian":
        subprocess.Popen(["bash", "-c", "cd %s && nohup python3 guardian.py >/tmp/guardian.log 2>&1 &" % HERE],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif name == "monitor":
        subprocess.Popen(["bash", "-c", "cd %s && nohup python3 feedback_monitor.py >/tmp/mon.log 2>&1 &" % HERE],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif name == "cloudflared":
        subprocess.Popen(["bash", "-c", "cd %s && nohup /tmp/cloudflared tunnel --no-autoupdate --config .tunnel/config.yml run 7ed54865-e16c-44fc-a03a-1708f9ae61d6 >>/tmp/cf-tunnel.log 2>&1 &" % HERE],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    alive = _proc_running(kind) > 0
    probe = ""
    if name == "server":
        try:
            c = _u.urlopen("http://127.0.0.1:8080/", timeout=6)
            probe = "HTTP " + str(c.status)
        except Exception:
            probe = "无响应"
    _audit(ip, "SVC_RESTART", "%s killed=%d alive=%s %s" % (name, len(killed), alive, probe))
    try:
        reg_set_admin("restart_" + name, time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    if not alive:
        return {"ok": False, "err": "重启后进程未存活，请到「日志」页排查"}
    return {"ok": True, "killed": len(killed), "probe": probe}


def _log_tail(name):
    paths = {
        "tunnel": "/tmp/cf-tunnel.log",
        "server": "/tmp/linuxsim_server.log",
        "guardian": "/tmp/guardian.log",
        "monitor": "/tmp/mon.log",
        "audit": AUDIT_LOG,
        "feedback_monitor": os.path.join(HERE, "feedback", "monitor_log.txt"),
    }
    p = paths.get(name)
    if not p or not os.path.exists(p):
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - 12000))
            return f.read().decode("utf-8", "replace")[-12000:]
    except Exception:
        return ""

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>系统管理 · %(host)s</title>
<style>
*{box-sizing:border-box;margin:0}
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#14131a;color:#e8e6ee;min-height:100vh;padding:20px}
.card{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);border-radius:14px;padding:16px;margin-bottom:14px;backdrop-filter:blur(16px)}
h1{font-size:20px;font-weight:600;margin-bottom:4px}
.sub{font-size:12px;opacity:.55;margin-bottom:18px}
input,select,textarea{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);border-radius:8px;color:#e8e6ee;padding:8px 10px;font-size:13.5px;width:100%%}
button{background:linear-gradient(180deg,#4f7cff,#3a5ee0);border:0;border-radius:8px;color:#fff;padding:8px 16px;font-size:13px;cursor:pointer}
button.gray{background:rgba(255,255,255,.1)}
button.red{background:linear-gradient(180deg,#e05b5b,#c73e3e)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kv{font-size:13px}.kv b{font-size:17px;display:block;margin-top:2px}
.ok{color:#52d68a}.bad{color:#ff7b6b}
.fb{border-top:1px solid rgba(255,255,255,.08);padding:10px 0;font-size:13px}
.fb .t{font-weight:600}
pre{background:rgba(0,0,0,.35);border-radius:10px;padding:12px;font-size:11.5px;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.tabs button{background:rgba(255,255,255,.08)}
.tabs button.on{background:linear-gradient(180deg,#4f7cff,#3a5ee0)}
.tag{font-size:11px;padding:2px 8px;border-radius:20px;background:rgba(255,255,255,.1);margin-left:6px}
.mut{opacity:.55;font-size:12px}
</style></head><body>
<div id="app"></div>
<script>
const $=s=>document.querySelector(s);
let SID=localStorage.getItem('adm_sid')||'';
async function api(p,d){
  const r=await fetch(p,{method:d?'POST':'GET',headers:{'Content-Type':'application/json','X-Adm-Sid':SID},body:d?JSON.stringify(d):undefined});
  if(r.status===401){SID='';localStorage.removeItem('adm_sid');login();throw new Error('未登录');}
  return r.json();
}
function login(){
  $('#app').innerHTML=`<div style="max-width:340px;margin:12vh auto" class="card">
    <h1>🔐 系统管理</h1><div class="sub">system.%(host)s · 最高权限</div>
    <div style="margin-bottom:10px"><input id="u" placeholder="用户名" autocomplete="username"></div>
    <div style="margin-bottom:14px"><input id="p" type="password" placeholder="密码" autocomplete="current-password"></div>
    <button id="go" style="width:100%%">登 录</button>
    <div id="st" style="font-size:12.5px;margin-top:10px;min-height:18px"></div></div>`;
  const doLogin=async()=>{
    $('#st').textContent='登录中…';
    try{
      const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({u:$('#u').value,p:$('#p').value})});
      const j=await r.json();
      if(j.ok){SID=j.sid;localStorage.setItem('adm_sid',SID);main();}
      else $('#st').textContent='登录失败：'+(j.err||'账号或密码不对');
    }catch(e){$('#st').textContent='网络异常：'+e.message;}
  };
  $('#go').onclick=doLogin;
  $('#p').onkeydown=e=>{if(e.key==='Enter')doLogin();};
}
function main(){
  $('#app').innerHTML=`
  <h1>🛠 cloudsystem 管理台</h1><div class="sub">system.%(host)s · 最高权限会话 · 操作全程审计</div>
  <div class="tabs">
    <button class="on" data-t="dash">📊 状态</button>
    <button data-t="fb">💬 反馈</button>
    <button data-t="chat">🤖 助手</button>
    <button data-t="term">⌨️ 终端</button>
    <button data-t="reg">🗂 注册表</button>
    <button data-t="svc">⚙ 服务</button>
    <button data-t="log">📜 日志</button>
    <button class="gray" id="out">退出</button>
  </div>
  <div id="view"></div>`;
  document.querySelectorAll('.tabs button[data-t]').forEach(b=>{
    b.onclick=()=>{document.querySelectorAll('.tabs button[data-t]').forEach(x=>x.classList.remove('on'));b.classList.add('on');
      ({dash:showDash,fb:showFb,svc:showSvc,log:showLog,chat:showChat,term:showTerm,reg:showReg})[b.dataset.t]();};
  });
  $('#out').onclick=async()=>{try{await api('/api/logout',{});}catch(e){}SID='';localStorage.removeItem('adm_sid');login();};
  showDash();
}
let termCwd="/workspace/linux-sim", termHist=[], termHi=0;
function showTerm(){
  $('#view').innerHTML=`<div class="card">
    <div style="font-weight:600;margin-bottom:6px">⌨️ root 终端（最高权限）</div>
    <div class="mut" style="margin-bottom:8px">真实服务器 shell，root 身份，30 秒超时，全程审计。cd 会自动跟踪。</div>
    <pre id="termOut" style="max-height:52vh;min-height:300px">root@server:${termCwd}# 就绪，输入命令开始（help 查看建议）</pre>
    <div class="row" style="margin-top:8px"><span style="font-size:12.5px;color:#52d68a;white-space:nowrap">$</span>
    <input id="termIn" autocomplete="off" spellcheck="false" style="flex:1;font-family:monospace" placeholder="输入命令，回车执行…"></div></div>`;
  const inp=$('#termIn');
  inp.focus();
  inp.onkeydown=async e=>{
    if(e.key==='Enter'&&inp.value.trim()){
      const cmd=inp.value.trim(); inp.value='';
      termHist.push(cmd); termHi=termHist.length;
      const out=$('#termOut');
      out.textContent+=`\\nroot@server:${termCwd}$ ${cmd}\\n`;
      out.textContent+='…执行中';
      try{
        const j=await api('/api/exec_root',{cmd,cwd:termCwd});
        const res=(j.out||'')+(j.err?(''+String.fromCharCode(10)+'[stderr] '+j.err):'');
        out.textContent=out.textContent.replace(/…执行中$/,''+String.fromCharCode(10));
        if(j.ok){ out.textContent+=res+String.fromCharCode(10); if(j.cwd) termCwd=j.cwd; }
        else out.textContent+='错误：'+j.err+String.fromCharCode(10);
        out.scrollTop=out.scrollHeight;
      }catch(e2){ out.textContent=out.textContent.replace(/…执行中$/,'\\n错误：'+e2.message+'\\n'); }
    }else if(e.key==='ArrowUp'){ if(termHi>0){termHi--; inp.value=termHist[termHi]||'';} e.preventDefault(); }
    else if(e.key==='ArrowDown'){ if(termHi<termHist.length-1){termHi++; inp.value=termHist[termHi]||'';} else {termHi=termHist.length; inp.value='';} e.preventDefault(); }
  };
}
async function showReg(){
  const v=$('#view'); v.textContent='加载中…';
  let nq={list:[]};
  try{ const rr=await fetch('/api/nickq?action=list',{headers:{'X-Adm-Sid':SID}}); nq=await rr.json(); }catch(e){}
  const pend=(nq.list||[]).filter(x=>x.status==='pending');
  try{
    const j=await api('/api/reg');
    v.innerHTML=`
    <div class="card"><div style="font-weight:600;margin-bottom:10px">✅ 昵称审核（AI 预筛 + 人工终审）<span class="tag" style="background:#e0a23c;color:#fff">${pend.length} 个待审</span></div>
      <div style="font-size:13px">${(nq.list||[]).map(x=>`<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-top:1px solid rgba(255,255,255,.08)">
        <b>${x.nick.replace(/</g,'&lt;')}</b><span class="mut">${x.grp}</span>
        <span class="tag" style="background:${x.status==='approved'?'#2b8f59':x.status==='rejected'?'#a33':'#b8860b'}">${x.status==='approved'?'已通过':x.status==='rejected'?'已拒绝':'待审核'}</span>
        <span class="mut" style="font-size:11px">AI建议：${(x.ai_note||'正常').replace(/</g,'&lt;')}</span>
        ${x.status==='pending'?`<span style="margin-left:auto;display:flex;gap:6px"><button onclick="nickAct('approve','${x.nick.replace(/'/g,'')}')" style="padding:4px 12px;border:0;border-radius:8px;background:#22b573;color:#fff;font-size:12px;cursor:pointer">通过</button><button onclick="nickAct('reject','${x.nick.replace(/'/g,'')}')" style="padding:4px 12px;border:0;border-radius:8px;background:#c73e3e;color:#fff;font-size:12px;cursor:pointer">拒绝</button></span>`:''}</div>`).join('')||'<div class="mut">暂无昵称申请</div>'}</div>
    <div class="card"><div style="font-weight:600;margin-bottom:10px">🗂 注册表（hive 键值）<span class="mut" style="font-weight:400;margin-left:8px">应用可在此注册配置与状态</span></div>
      <div style="font-size:13px">${(j.hive||[]).map(h=>`<div style="padding:6px 0;border-top:1px solid rgba(255,255,255,.07)"><b>${h.k}</b> = <span class="ok">${(h.v+'').replace(/</g,'&lt;')}</span> <span class="mut">${h.ts}</span></div>`).join('')||'<div class="mut">空</div>'}</div>
      <div class="row" style="margin-top:10px"><input id="regK" placeholder="键名" style="max-width:150px"><input id="regV" placeholder="值" style="flex:1;min-width:150px"><button onclick="regSet()">注册</button></div></div>
    <div class="card"><div style="font-weight:600;margin-bottom:10px">📡 系统事件流（自动注册）</div>
      <div style="font-size:12.5px">${(j.events||[]).map(e=>`<div style="padding:4px 0;border-top:1px solid rgba(255,255,255,.06)"><span class="tag">${e.type}</span> ${e.detail.replace(/</g,'&lt;')} <span class="mut" style="float:right">${e.ts}</span></div>`).join('')||'<div class="mut">暂无事件</div>'}</div></div>`;
  }catch(e){ v.textContent='加载失败：'+e.message; }
}
window.nickAct=async(act,nick)=>{
  const r=await fetch('/api/nickq',{method:'POST',headers:{'Content-Type':'application/json','X-Adm-Sid':SID},body:JSON.stringify({action:act,nick})});
  const j=await r.json();
  if(j.ok) showReg(); else alert('失败：'+(j.err||''));
};
async function regSet(){
  const k=$('#regK').value.trim(), val=$('#regV').value.trim();
  if(!k){ alert('先填键名'); return; }
  const r=await fetch('/api/reg',{method:'POST',headers:{'Content-Type':'application/json','X-Adm-Sid':SID},body:JSON.stringify({action:'set',k,v:val})});
  if((await r.json()).ok) showReg(); else alert('注册失败');
}
let chatTimer=null;
async function showChat(){
  if(chatTimer) clearInterval(chatTimer);
  $('#view').innerHTML=`<div class="card">
    <div style="font-weight:600;margin-bottom:6px">🤖 助手（WorkBuddy 在线）</div>
    <div class="mut" style="margin-bottom:10px">直接发消息，助手会尽快回复（秒级~分钟级）。也可以让它查状态、重启服务、分析问题。</div>
    <div id="msgs" style="max-height:46vh;overflow:auto;border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:10px;margin-bottom:10px"></div>
    <div class="row"><input id="chatIn" placeholder="输入消息…" style="flex:1">
    <button id="chatGo">发送</button></div></div>`;
  const pull=async ()=>{
    try{
      const j=await api('/api/chat');
      const m=$('#msgs'); if(!m) return;
      m.innerHTML=(j.list||[]).map(x=>{
        const mine=x.role==='user';
        return `<div style="margin:6px 0;text-align:${mine?'right':'left'}">
          <div style="display:inline-block;max-width:85%%;padding:7px 11px;border-radius:12px;font-size:13px;
            background:${mine?'rgba(79,124,255,.25)':'rgba(82,214,138,.15)'};white-space:pre-wrap;word-break:break-word">${x.text.replace(/</g,'&lt;')}</div>
          <div class="mut" style="font-size:10.5px;margin-top:2px">${x.ts} · ${mine?'我':'助手'}</div></div>`;
      }).join('')||'<div class="mut">还没有消息，打个招呼吧</div>';
      m.scrollTop=m.scrollHeight;
    }catch(e){}
  };
  $('#chatGo').onclick=async ()=>{
    const i=$('#chatIn'), v=i.value.trim();
    if(!v) return; i.value='';
    await api('/api/chat',{msg:v}); pull();
  };
  $('#chatIn').onkeydown=e=>{ if(e.key==='Enter')$('#chatGo').onclick(); };
  await pull();
  chatTimer=setInterval(pull, 3000);
}
async function showDash(){
  const v=$('#view');v.textContent='加载中…';
  try{
    const s=await api('/api/status');
    const svc=k=>s.services[k]?`<span class="ok">● 运行中</span>`:`<span class="bad">● 已停止</span>`;
    const restarts=s.restarts||{};
    const rstamp=k=>restarts[k]?'<span class="mut" style="font-size:10.5px">（最近重启 '+restarts[k]+'）</span>':'';
    v.innerHTML=`
    <div class="card"><div style="font-weight:600;margin-bottom:10px">站点</div>
      <div class="grid">
        <div class="kv">当前版本<b>${s.version||'?'}</b></div>
        <div class="kv">反馈（已修复）<b>${s.fb_fixed}</b></div>
        <div class="kv">反馈（待处理）<b>${s.fb_pending}</b></div>
        <div class="kv">运行时长<b>${s.uptime}</b></div>
      </div></div>
    <div class="card"><div style="font-weight:600;margin-bottom:10px">主机</div>
      <div class="grid">
        <div class="kv">内存<b>${s.mem.avail_gb} / ${s.mem.total_gb} GB 可用</b></div>
        <div class="kv">磁盘<b>${s.disk.used_gb} / ${s.disk.total_gb} GB（${s.disk.pct}%%）</b></div>
        <div class="kv">负载<b>${s.load}</b></div>
      </div></div>
    <div class="card"><div style="font-weight:600;margin-bottom:10px">服务</div>
      <div class="grid">
        <div class="kv">主站 server<b>${svc('server')}</b>${rstamp('server')}</div>
        <div class="kv">隧道 cloudflared<b>${svc('cloudflared')}</b>${rstamp('cloudflared')}</div>
        <div class="kv">守护 guardian<b>${svc('guardian')}</b>${rstamp('guardian')}</div>
        <div class="kv">反馈巡检 monitor<b>${svc('feedback_monitor')}</b>${rstamp('monitor')}</div>
        <div class="kv">本管理台<b class="ok">● 运行中</b></div>
      </div></div>`;
  }catch(e){v.textContent='加载失败：'+e.message;}
}
async function showFb(){
  const v=$('#view');v.textContent='加载中…';
  try{
    const j=await api('/api/feedback');
    v.innerHTML=`<div class="card">${j.list.length? j.list.map(f=>`
      <div class="fb">
        <div><span class="t">${f.title}</span>
          <span class="tag">${f.fixed?'✓ 已修复':'待处理'}</span>
          <span class="mut">${f.ts} · ${f.type}</span></div>
        <div style="margin:6px 0;opacity:.85">${(f.detail||'').replace(/</g,'&lt;')}</div>
        ${f.reply?`<div class="mut">↩ ${f.reply.replace(/</g,'&lt;')}</div>`:''}
        <div class="row" style="margin-top:8px">
          <input id="rp_${f.id}" placeholder="回复内容" style="max-width:300px" value="${(f.reply||'').replace(/"/g,'&quot;')}">
          <button onclick="fbAct('${f.id}','fix')">标记修复</button>
          <button class="gray" onclick="fbAct('${f.id}','unfix')">取消修复</button>
          <button class="gray" onclick="fbAct('${f.id}','reply')">保存回复</button>
        </div>
      </div>`).join(''):'<div class="mut">暂无反馈</div>'}</div>`;
  }catch(e){v.textContent='加载失败：'+e.message;}
}
window.fbAct=async(id,act)=>{
  const reply=(act==='reply')?$('#rp_'+id).value:undefined;
  const j=await api('/api/feedback_action',{id,action:act,reply});
  if(!j.ok)alert('失败：'+(j.err||''));else showFb();
};
function showSvc(){
  const v=$('#view');
  v.innerHTML=`<div class="card"><div style="font-weight:600;margin-bottom:10px">服务操作（重启会短暂中断）</div>
    <div class="row">
      <button onclick="svc('server')">重启主站 server</button>
      <button onclick="svc('cloudflared')">重启隧道 cloudflared</button>
      <button onclick="svc('guardian')">重启守护 guardian</button>
      <button onclick="svc('monitor')">重启反馈巡检 monitor</button>
    </div>
    <div class="mut" style="margin-top:10px">重启后建议到「状态」页确认各服务恢复运行。</div></div>`;
}
window.svc=async(name)=>{
  if(!confirm('确认重启 '+name+' ？'))return;
  const j=await api('/api/service',{name});
  alert(j.ok?'✅ 重启成功：杀掉旧进程 '+j.killed+' 个'+(j.probe?('，探活 '+j.probe):'')+'（状态页可见「最近重启」）':'❌ 失败：'+(j.err||''));
};
async function showLog(){
  const v=$('#view');
  v.innerHTML=`<div class="card"><div class="tabs">
      <button class="on" data-l="tunnel">隧道</button><button data-l="server">主站</button>
      <button data-l="guardian">守护</button><button data-l="monitor">巡检</button>
      <button data-l="feedback_monitor">巡检报告</button><button data-l="audit">管理审计</button>
    </div><pre id="lg">加载中…</pre></div>`;
  const load=async n=>{
    document.querySelectorAll('.tabs [data-l]').forEach(x=>x.classList.toggle('on',x.dataset.l===n));
    $('#lg').textContent=(await api('/api/log?name='+n)).text||'（空）';
  };
  document.querySelectorAll('.tabs [data-l]').forEach(b=>b.onclick=()=>load(b.dataset.l));
  load('tunnel');
}
if(SID){api('/api/status').then(main).catch(()=>{});}else{login();}
</script></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _ip(self):
        return self.client_address[0] if self.client_address else "?"

    def _hdr(self, code=200, ctype="application/json; charset=utf-8", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _json(self, obj, code=200, cookie=None):
        self._hdr(code, cookie=cookie)
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode())

    def _sid(self):
        # Cookie 或 header 均可
        c = self.headers.get("Cookie", "")
        for part in c.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "adm_sid":
                return v
        return self.headers.get("X-Adm-Sid", "")

    def _authed(self):
        sid = self._sid()
        with _sess_lock:
            exp = SESSIONS.get(sid)
            if exp and exp > time.time():
                SESSIONS[sid] = time.time() + SESSION_TTL  # 滑动续期
                return True
            SESSIONS.pop(sid, None)
        return False

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}


    # ---- AI 助手对话（消息箱：管理台发问 → agent 巡检回复）----

    # ---- root 终端（最高权限，仅管理台会话可用，全程审计）----
    def _exec_root(self, data, ip):
        cmd = str(data.get("cmd", "")).strip()
        cwd = str(data.get("cwd", "")).strip() or "/workspace/linux-sim"
        if not cmd:
            return self._json({"ok": False, "err": "空命令"})
        now = time.time()
        hits = [t for t in _exec_hits.get(ip, []) if now - t < 60]
        if len(hits) >= 30:
            _exec_hits[ip] = hits
            return self._json({"ok": False, "err": "太快了（每分钟 30 条上限）"})
        _exec_hits[ip] = hits + [now]
        try:
            r = subprocess.run(
                ["bash", "-c", 'cd ' + shlex.quote(cwd) + ' 2>/dev/null || cd /; ' + cmd
                 + '; printf "\n__CWD__%s" "$PWD"'],
                capture_output=True, text=True, timeout=30)
            out = (r.stdout or "")
            err = (r.stderr or "")[-3000:]
        except subprocess.TimeoutExpired:
            return self._json({"ok": False, "err": "超时（30 秒上限），长任务请 nohup 后台跑"})
        except Exception as e:
            return self._json({"ok": False, "err": str(e)})
        newcwd = cwd
        if "__CWD__" in out:
            out, _, tail = out.rpartition("__CWD__")
            newcwd = tail.strip() or cwd
            out = out.rstrip("\n")
        if len(out) > 8000:
            out = out[-8000:] + "\n…（输出已截断）"
        _audit(ip, "ROOT_EXEC", cmd[:80])
        return self._json({"ok": True, "out": out, "err": err, "cwd": newcwd})


    # ---- 模拟注册表（hive 键值 + 系统事件流，sqlite 直读）----
    def _reg_all(self):
        import sqlite3
        db = os.path.join(HERE, "reg.db")
        if not os.path.exists(db):
            return {"hive": [], "events": []}
        try:
            c = sqlite3.connect(db, timeout=5)
            c.execute("CREATE TABLE IF NOT EXISTS hive(k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, detail TEXT)")
            hive = [{"k": r[0], "v": r[1], "ts": r[2]} for r in c.execute("SELECT k,v,ts FROM hive ORDER BY k")]
            events = [{"ts": r[0], "type": r[1], "detail": r[2]} for r in c.execute("SELECT ts,type,detail FROM events ORDER BY id DESC LIMIT 60")]
            c.close()
            return {"hive": hive, "events": events}
        except Exception as e:
            return {"hive": [], "events": [], "err": str(e)}


    # ---- 昵称审核（approve/reject 直写 friends.db）----
    def _nickq(self, data, ip):
        import sqlite3
        db = os.path.join(HERE, "feedback", "friends.db")
        act = str(data.get("action", ""))
        try:
            c = sqlite3.connect(db, timeout=8)
            c.execute("CREATE TABLE IF NOT EXISTS nick_status(nick TEXT PRIMARY KEY, grp TEXT, status TEXT, ts TEXT)")
            if act == "list":
                try: c.execute("ALTER TABLE nick_status ADD COLUMN ai_note TEXT")
                except Exception: pass
                rows = [{"nick": r[0], "grp": r[1], "status": r[2], "ts": r[3], "ai_note": r[4] if len(r)>4 else "正常"}
                        for r in c.execute("SELECT nick,grp,status,ts,ai_note FROM nick_status ORDER BY ts DESC LIMIT 50")]
                c.close(); return {"ok": True, "list": rows}
            if act in ("approve", "reject"):
                nick = str(data.get("nick", ""))
                st = "approved" if act == "approve" else "rejected"
                c.execute("UPDATE nick_status SET status=? WHERE nick=?", (st, nick))
                c.commit(); c.close()
                _audit(ip, "NICK_" + act.upper(), nick)
                return {"ok": True}
            c.close()
        except Exception as e:
            return {"ok": False, "err": str(e)}
        return {"ok": False, "err": "action 不合法"}

    def _chat_file(self):
        # 文档式对话：聊天记录即 feedback/admin_chat_doc.txt（纯文本，监测脚本按大小轮询）
        return os.path.join(HERE, "feedback", "admin_chat_doc.txt")

    def _chat_read(self):
        out = []
        cur = None
        try:
            for ln in open(self._chat_file(), encoding="utf-8"):
                if ln.startswith("👤") or ln.startswith("🤖"):
                    if cur is not None:
                        out.append(cur)
                    cur = {"role": "user" if ln.startswith("👤") else "agent",
                           "ts": ln.split("：", 1)[0].replace("👤 用户", "")
                                      .replace("🤖 助手", "").strip(),
                           "text": (ln.split("：", 1)[1].strip()
                                    if "：" in ln else "")}
                else:
                    if cur is not None:
                        cur["text"] += "\n" + ln.rstrip("\n")
        except Exception:
            pass
        if cur is not None:
            out.append(cur)
        return out[-80:]

    def _chat_append(self, role, text):
        # 网页聊天框发消息 → 以「👤 用户」格式追加进文档（监测脚本会读并回复）
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        block = f"\n👤 用户 {ts}：{str(text)[:3000]}\n"
        try:
            with open(self._chat_file(), "a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(block)
        except Exception:
            pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            host = "3920850391.dpdns.org"
            self._hdr(200, "text/html; charset=utf-8")
            self.wfile.write((PAGE % {"host": host}).encode())
            return
        if self.path.startswith("/api/status"):
            if not self._authed(): return self._json({"ok": False}, 401)
            return self._json(_host_status())
        if self.path.startswith("/api/feedback"):
            if not self._authed(): return self._json({"ok": False}, 401)
            return self._json({"ok": True, "list": _fb_read_all()})
        if self.path.startswith("/api/nickq"):
            if not self._authed(): return self._json({"ok": False}, 401)
            d = self._body() if self.command == "POST" else {}
            return self._json(self._nickq(d, self._ip()))
        if self.path.startswith("/api/reg"):
            if not self._authed(): return self._json({"ok": False}, 401)
            return self._json(self._reg_all())
        if self.path.startswith("/api/chat"):
            if not self._authed(): return self._json({"ok": False}, 401)
            return self._json({"ok": True, "list": self._chat_read()})
        if self.path.startswith("/api/log"):
            if not self._authed(): return self._json({"ok": False}, 401)
            name = self.path.split("name=")[-1].split("&")[0]
            return self._json({"ok": True, "text": _log_tail(name)})
        return self._json({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        ip = self._ip()
        if self.path == "/login":
            if not _login_allowed(ip):
                return self._json({"ok": False, "err": "失败次数过多，10 分钟后再试"})
            d = self._body()
            u_ok = hmac.compare_digest(str(d.get("u", "")), ADMIN_USER)
            p_ok = hmac.compare_digest(str(d.get("p", "")), ADMIN_PASS)
            if u_ok and p_ok:
                sid = secrets.token_urlsafe(32)
                with _sess_lock:
                    SESSIONS[sid] = time.time() + SESSION_TTL
                _audit(ip, "LOGIN_OK", "")
                return self._json({"ok": True, "sid": sid},
                                  cookie=f"adm_sid={sid}; Secure; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}; Path=/")
            _login_fail(ip)
            _audit(ip, "LOGIN_FAIL", str(d.get("u", ""))[:30])
            return self._json({"ok": False, "err": "账号或密码不对"})
        if not self._authed():
            return self._json({"ok": False}, 401)
        d = self._body()
        if self.path == "/api/logout":
            with _sess_lock:
                SESSIONS.pop(self._sid(), None)
            return self._json({"ok": True})
        if self.path == "/api/nickq":
            return self._json(self._nickq(d, ip))
        if self.path == "/api/exec_root":
            return self._exec_root(d, ip)
        if self.path == "/api/chat":
            msg = str(d.get("msg", "")).strip()[:3000]
            if not msg:
                return self._json({"ok": False, "err": "内容不能为空"})
            self._chat_append("user", msg)
            _audit(ip, "CHAT", msg[:60])
            return self._json({"ok": True})
        if self.path == "/api/feedback_action":
            return self._json(_fb_action(d, ip))
        if self.path == "/api/service":
            return self._json(_service_restart(str(d.get("name", "")), ip))
        return self._json({"ok": False, "err": "not found"}, 404)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    print(f"[admin] 监听 127.0.0.1:{PORT} （经 Cloudflare Tunnel: system.3920850391.dpdns.org）")
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()

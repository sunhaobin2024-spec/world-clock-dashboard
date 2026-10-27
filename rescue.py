#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""最外层看门狗 —— 守着已经死掉的 guardian。

这一版改用遍历 /proc 找进程，**不再用 pgrep -f**。
原因：pgrep -f / `ps | grep` 匹配的是完整命令行，而执行这条命令的
shell 自己的命令行里就含 "server.py"，所以它永远会匹配到自己，
误判成"进程还在"。这个坑已经踩了三次（watchdog.sh stop 自杀、
杀云层隧道误杀调用方、rescue.sh 自我匹配），不能再用字符串匹配。

/proc/<pid>/cmdline 是 NUL 分隔的 argv 数组，第一个元素是真实可执行文件
路径 —— 拿它做判断，不受调用方命令行干扰。
"""
import os
import subprocess
import time

HERE = "/workspace/linux-sim"
PY_BIN = "/root/.pyenv/versions/3.11.1/bin/python3.11"
CF_BIN = "/tmp/cloudflared"
LOG = "/tmp/rescue.log"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def procs():
    """返回 [(pid, argv列表)]，跳过自己。"""
    me = os.getpid()
    out = []
    try:
        names = os.listdir("/proc")
    except Exception:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == me:
            continue
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                raw = f.read()
        except Exception:
            continue
        argv = [x.decode("utf-8", "replace")
                for x in raw.split(b"\0") if x]
        if not argv:
            continue
        out.append((pid, argv))
    return out


def find(needle, exact_exe=None):
    """找进程：argv 里含 needle，且可执行文件路径匹配 exact_exe。

    排除 shell/grep：它们只是命令行里"提到了"目标，不代表目标在跑。
    """
    hit = []
    for pid, argv in procs():
        joined = " ".join(argv)
        if needle not in joined:
            continue
        if "rescue.py" in joined:
            continue
        exe = argv[0]
        base = os.path.basename(exe)
        if base in ("grep", "zsh", "bash", "sh", "pgrep", "ps"):
            continue
        if exact_exe and not exe.startswith(exact_exe):
            continue
        hit.append(pid)
    return hit


def spawn(cmd, logfile, cwd=HERE):
    with open(logfile, "a", buffering=1) as f:
        subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=cwd,
                         start_new_session=True)


def main():
    log("rescue 启动（/proc 精确匹配版）")
    while True:
        try:
            g = find("guardian.py", exact_exe=PY_BIN)
            s = find("server.py", exact_exe=PY_BIN)
            c = find("cloudflared", exact_exe=CF_BIN)

            if not g:
                spawn([PY_BIN, os.path.join(HERE, "guardian.py")],
                      "/tmp/guardian-rescue.log")
                log("guardian 已拉起")
                time.sleep(8)          # 守护自己会拉后端
                continue

            if not s:
                spawn([PY_BIN, os.path.join(HERE, "server.py")],
                      "/tmp/linuxsim.log")
                log("后端已拉起")
            r = find("report_daemon.py", exact_exe=PY_BIN)
            if not r:
                spawn([PY_BIN, os.path.join(HERE, "report_daemon.py")],
                      "/tmp/report-daemon.log")
                log("日报守护已拉起")
            # VNC 远程桌面栈（noVNC）—— 独立自愈
            # 注意：websockify 实际以 "python3 /usr/bin/websockify ..." 运行，
            # argv[0] 是 python3 而非 websockify，故不能按 exact_exe 精确匹配，
            # 否则永远误判"没在跑"，每 15s 拆一次 VNC 栈（已踩坑）。
            vws = find("websockify")
            vnc = find("x11vnc", exact_exe="/usr/bin/x11vnc")
            if not vws or not vnc:
                spawn(["/bin/bash", os.path.join(HERE, "vnc.sh")],
                      "/tmp/vnc-rescue.log")
                log("VNC 远程桌面栈已拉起")
            # 心跳保活守护（借鉴 ACE 反外挂持续心跳机制，对抗沙箱空闲回收）
            hb = find("heartbeat.py", exact_exe=PY_BIN)
            if not hb:
                spawn([PY_BIN, os.path.join(HERE, "heartbeat.py")],
                      "/tmp/heartbeat.log")
                log("心跳保活守护已拉起")
            # 注：隧道不再由 rescue 补充。永久域名走 Cloudflare Named
            # Tunnel，完全由 guardian.py 负责拉起/重连；这里若补 Quick
            # 随机隧道会泄漏临时域名，与"停用所有 trycloudflare 地址"冲突。
        except Exception as e:
            log("rescue 异常: %r" % (e,))
        time.sleep(15)


if __name__ == "__main__":
    main()

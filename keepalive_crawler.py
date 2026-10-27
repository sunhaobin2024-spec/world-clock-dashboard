#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保活爬虫：定时访问公网站点，制造真实入站流量以顶住沙箱空闲回收。
- 每 INTERVAL 秒 GET 主站 + VNC 页面（带浏览器 UA，走 Cloudflare->隧道->server）
- 主站连续 FAIL_THRESHOLD 次非 200/302 才触发自愈（防网络抖动误伤）
- 自愈先辨故障层：本地 8080 正常但公网异常 = 隧道问题，只补 cloudflared；
  本地也挂才拉 rescue 看门狗；补 VNC 栈前查端口，已监听不重复起
- 状态追加写入 .keepalive_status
用法：setsid python3 keepalive_crawler.py  (后台常驻)
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，先 pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, ".keepalive_status")
VNC_PASS = "575006"
INTERVAL = 80          # 探测间隔（秒）
STARTUP_GRACE = 45     # 启动宽限期：等 cloudflared 隧道/VNC 栈就绪
TIMEOUT = 12
FAIL_THRESHOLD = 2     # 连续 N 次公网异常才自愈
HEAL_COOLDOWN = 120    # 自愈后静默期，避免连环误判
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
URLS = {
    "主站": "https://3920850391.dpdns.org/",
    "VNC": "https://vnc.3920850391.dpdns.org/vnc.html",
}
LOCAL_MAIN = "http://127.0.0.1:8080/"


def log(line):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")


def probe(url):
    """返回 (状态码, 字节数)；异常返回 (0, 0)"""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        return r.status_code, len(r.content)
    except Exception:
        return 0, 0


def port_listening(port):
    """本地端口是否在监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def rescue_running():
    try:
        out = subprocess.run(["pgrep", "-f", "linux-sim/rescue.py"],
                             capture_output=True, text=True)
        return bool(out.stdout.strip())
    except Exception:
        return False


def heal_tunnel():
    """只补 cloudflared（本地正常、公网异常 = 隧道断）"""
    log("判定: 本地正常/公网异常 -> 补隧道 cloudflared")
    subprocess.Popen(
        ["setsid", "python3", os.path.join(HERE, "rescue.py")],
        stdout=open("/tmp/rescue_boot.log", "a"), stderr=subprocess.STDOUT)


def heal_full():
    """本地也挂 -> 拉 rescue 看门狗（它会起 server/cloudflared/heartbeat）"""
    log("判定: 本地也异常 -> 拉 rescue 看门狗")
    subprocess.Popen(
        ["setsid", "python3", os.path.join(HERE, "rescue.py")],
        stdout=open("/tmp/rescue_boot.log", "a"), stderr=subprocess.STDOUT)
    time.sleep(14)
    heal_vnc()


def heal_vnc():
    """补 VNC 栈，端口已监听则跳过"""
    if not port_listening(5901):
        subprocess.Popen(
            ["setsid", "x11vnc", "-display", ":1", "-passwd", VNC_PASS,
             "-forever", "-shared", "-nolookup", "-noipv6", "-rfbport", "5901"],
            stdout=open("/tmp/x11vnc.log", "a"), stderr=subprocess.STDOUT)
        time.sleep(2)
    if not port_listening(6080):
        subprocess.Popen(
            ["setsid", "/usr/bin/websockify", "--web", "/usr/share/novnc",
             "6080", "localhost:5901"],
            stdout=open("/tmp/websockify.log", "a"), stderr=subprocess.STDOUT)


def main():
    log("保活爬虫启动")
    log(f"启动宽限 {STARTUP_GRACE}s（等隧道/VNC 栈就绪）")
    time.sleep(STARTUP_GRACE)
    fail_streak = 0
    last_heal = 0.0
    while True:
        parts = []
        main_ok = False
        for name, url in URLS.items():
            code, size = probe(url)
            parts.append(f"{name}={code}({size}B)")
            if name == "主站" and code in (200, 302):
                main_ok = True
        line = " ".join(parts)
        log(line)

        if main_ok:
            fail_streak = 0
        else:
            fail_streak += 1
            now = time.time()
            # 连续 N 次异常 + 距上次自愈超过冷却期，才动手
            if fail_streak >= FAIL_THRESHOLD and now - last_heal >= HEAL_COOLDOWN:
                last_heal = now
                local_code, _ = probe(LOCAL_MAIN)
                if local_code in (200, 302):
                    heal_tunnel()   # 隧道问题，只补隧道
                else:
                    heal_full()     # 源站也挂，全量拉起
                time.sleep(STARTUP_GRACE)  # 自愈后宽限，等栈就绪再探
                fail_streak = 0
            else:
                log(f"公网异常 {fail_streak}/{FAIL_THRESHOLD} 次，暂不自愈（防误伤）")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

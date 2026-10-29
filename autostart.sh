#!/bin/sh
# 沙箱休眠/重启后自动拉起整套服务。
HERE="$(cd "$(dirname "$0")" && pwd)"
CF=/tmp/cloudflared

# 守护进程自己会管后端和隧道（每 10s 巡检），只要保证它活着。
if ! ps -eo args --no-headers | grep -q "[g]uardian\.py"; then
    setsid nohup python3.11 "$HERE/guardian.py" >/dev/null 2>&1 </dev/null &
fi

# 管理台（8443，system.3920850391.dpdns.org）由 admin_keepalive.sh 每 30s 探活自愈。
if ! ps -eo args --no-headers | grep -q "[a]dmin_keepalive\.sh"; then
    setsid nohup bash "$HERE/admin_keepalive.sh" >/tmp/admin_keepalive.log 2>&1 </dev/null &
fi

# noVNC 远程桌面（6080，vnc.3920850391.dpdns.org）由 vnc_keepalive.sh 保活。
if ! ps -eo args --no-headers | grep -q "[v]nc_keepalive\.sh"; then
    setsid nohup bash "$HERE/vnc_keepalive.sh" >/tmp/vnc_keepalive.log 2>&1 </dev/null &
fi

# 64 位虚拟机隧道线（8081，vm64.3920850391.dpdns.org）由 vm64_keepalive.sh 保活。
if ! ps -eo args --no-headers | grep -q "[v]m64_keepalive\.sh"; then
    setsid nohup bash "$HERE/vm64_keepalive.sh" >/tmp/vm64_keepalive.log 2>&1 </dev/null &
fi

# 反馈巡检守护（feedback_monitor，默认 60s 一轮；可用 env FB_INTERVAL 覆盖）
if ! ps -eo args --no-headers | grep -q "[f]eedback_monitor\.py"; then
    setsid nohup python3.11 "$HERE/feedback_monitor.py" >/tmp/mon.log 2>&1 </dev/null &
fi

# Named Tunnel 边缘连接保活（每 10s 探活，防冷连接导致首字节变慢）
if ! ps -eo args --no-headers | grep -q "[t]unnel_keepalive\.sh"; then
    setsid nohup bash "$HERE/tunnel_keepalive.sh" >/tmp/tunnel_keepalive.log 2>&1 </dev/null &
fi

# 管理后台聊天箱监测（admin_chat_monitor，默认 60s 一轮；ADMIN_CHAT_INTERVAL 可覆盖）
if ! ps -eo args --no-headers | grep -q "[a]dmin_chat_monitor\.py"; then
    setsid nohup python3.11 "$HERE/admin_chat_monitor.py" >/tmp/admin_chat_mon.log 2>&1 </dev/null &
fi

# 守护只管 Named Tunnel（http2）。等它异步拉起（实测约 19s），再决定是否补 quick 兜底。
for _ in $(seq 1 40); do
    if ps -eo args --no-headers | grep -q "[c]loudflared tunnel --no-autoupdate --protocol http2"; then
        break
    fi
    sleep 1
done
if ! ps -eo args --no-headers | grep -q "[c]loudflared tunnel --no-autoupdate --protocol http2"; then
    setsid nohup "$CF" tunnel --no-autoupdate --url http://127.0.0.1:8080 \
        >/tmp/cf-quick.log 2>&1 </dev/null &
fi

# Minecraft 服务器（25565，mc.3920850391.dpdns.org 走隧道 TCP 入口）
# 沙箱休眠会被杀，每次对话 autostart 都跑到这里自然自愈；已有进程则跳过（幂等）。
if [ -x "$HERE/mc-server/start.sh" ]; then
    if ! ss -ltn 2>/dev/null | grep -q ":25565" && ! pgrep -f "server\.jar" >/dev/null 2>&1; then
        setsid nohup bash "$HERE/mc-server/start.sh" >/tmp/mc_autostart.log 2>&1 </dev/null &
    fi
fi

#!/bin/sh
# 沙箱休眠/重启后自动拉起整套服务。
# 装法：把下面这行加进 crontab（crontab -e），用 @reboot：
#   @reboot /workspace/linux-sim/autostart.sh >/dev/null 2>&1
#
# 为什么需要：沙箱每次休眠唤醒，进程都被清空，之前要等你发现
# 报"打不开了"我才手工拉。要做到你永远不用管，就必须挂在
# 系统的启动钩子上。
HERE="$(cd "$(dirname "$0")" && pwd)"
CF=/tmp/cloudflared

# 守护进程自己会管后端和隧道（每 10s 巡检），所以只要保证它活着。
if ! ps -eo args --no-headers | grep -q "[g]uardian\.py"; then
    setsid nohup python3.11 "$HERE/guardian.py" >/dev/null 2>&1 </dev/null &
fi

# 管理台（8443，system.3920850391.dpdns.org）不在 guardian 管辖内，
# 由 admin_keepalive.sh 每 30s 探活自愈，这里负责把它拉起来。
if ! ps -eo args --no-headers | grep -q "[a]dmin_keepalive\.sh"; then
    setsid nohup bash "$HERE/admin_keepalive.sh" >/tmp/admin_keepalive.log 2>&1 </dev/null &
fi

# noVNC 远程桌面（6080，vnc.3920850391.dpdns.org）由 vnc_keepalive.sh
# 每 30s 调 vnc.sh 幂等保活，这里负责把它拉起来。
if ! ps -eo args --no-headers | grep -q "[v]nc_keepalive\.sh"; then
    setsid nohup bash "$HERE/vnc_keepalive.sh" >/tmp/vnc_keepalive.log 2>&1 </dev/null &
fi

# 64 位虚拟机隧道线（8081，vm64.3920850391.dpdns.org）由 vm64_keepalive.sh
# 每 30s 保活 websockify 8081->5902（与 vnc 桌面 5901 错开，并存不打架）。
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

# 守护只管 Named Tunnel（已关闭），Quick Tunnel 断了它不自动重连
# ——这是故意的：重连会换域名，偷偷换掉你手机里的链接。
# 但"全挂了没人拉"和"还在服务只是连接断了"是两回事：
# 前者必须自动恢复，后者要等你决定。所以只补进程，不动已活着的隧道。
sleep 5
if ! ps -eo args --no-headers | grep -q "[c]loudflared tunnel"; then
    setsid nohup "$CF" tunnel --no-autoupdate --url http://127.0.0.1:8080 \
        >/tmp/cf-quick.log 2>&1 </dev/null &
fi

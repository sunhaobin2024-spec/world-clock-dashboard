#!/bin/bash
# vm64_keepalive.sh —— 64 位虚拟机隧道线保活（常驻循环）
# 只保活 websockify 8081 -> 5902（与 vnc 桌面 5901 错开端口，互不打架）
# QEMU 虚拟机实例仍按需（管理台「应用商店」点启动），本脚本不拉 QEMU，避免常驻吃内存。
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="/tmp/vm64_keepalive.lock"
PORT_CHK="8081"

is_listening() {
    # 端口是否在监听（TCP LISTEN）
    (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]${PORT_CHK}[[:space:]]"
}

while true; do
(
    flock -n 9 || exit 0
    if ! is_listening; then
        echo "[$(date '+%F %T')] 8081 未监听，拉起 websockify 8081->5902"
        setsid nohup websockify --heartbeat 30 --web /usr/share/novnc 0.0.0.0:8081 127.0.0.1:5902 \
            >/tmp/ws64_keepalive.log 2>&1 </dev/null &
    fi
) 9>"$LOCK"
    sleep 30
done

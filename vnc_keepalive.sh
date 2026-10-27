#!/bin/bash
# noVNC 远程桌面保活：每轮检测 6080 端口是否真正在监听，
# 没监听才调 vnc.sh 拉起，并等其就绪后再进入下一轮（30s），
# 避免启动慢导致的并发重复拉起。flock 防自身多实例。
HERE="$(cd "$(dirname "$0")" && pwd)"
exec 9>/tmp/vnc_keepalive.lock
flock -n 9 || exit 0

port_listen() {
    (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE ":6080 "
}

while true; do
    if ! port_listen; then
        setsid nohup bash "$HERE/vnc.sh" >/tmp/vnc.log 2>&1 </dev/null &
        # 等 websockify 真正 bind 完成，最多 25s
        for _ in $(seq 1 25); do
            port_listen && break
            sleep 1
        done
    fi
    sleep 30
done

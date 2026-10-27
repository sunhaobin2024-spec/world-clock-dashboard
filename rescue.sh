#!/bin/sh
# 最外层看门狗：守着一个已经死掉的 guardian。
#
# 为什么需要这层：沙箱休眠唤醒时所有进程都被清空，包括 guardian 自己。
# 沙箱里没有 systemd、cron 也只有 @reboot（而 @reboot 在容器里根本不触发），
# 所以真正能生效的办法只有一个 —— 在一个还能跑的地方挂个循环，
# 一旦发现守护不在就拉起来。
#
# 它同时负责补后端和隧道（守护也会做，这里是双保险）。
HERE=/workspace/linux-sim
CF=/tmp/cloudflared

while true; do
    if ! ps -eo args --no-headers 2>/dev/null | grep -q "[g]uardian\.py"; then
        setsid nohup python3.11 "$HERE/guardian.py" \
            >/dev/null 2>&1 </dev/null &
        echo "$(date '+%F %T') rescue: guardian 已拉起" >> /tmp/rescue.log
    fi
    if ! ps -eo args --no-headers 2>/dev/null | grep -q "[s]erver\.py"; then
        cd "$HERE" && setsid nohup python3.11 "$HERE/server.py" \
            >>/tmp/linuxsim.log 2>&1 </dev/null &
        echo "$(date '+%F %T') rescue: 后端已拉起" >> /tmp/rescue.log
    fi
    if ! ps -eo args --no-headers 2>/dev/null | grep -q "[c]loudflared tunnel"; then
        setsid nohup "$CF" tunnel --no-autoupdate \
            --url http://127.0.0.1:8080 >>/tmp/cf-guard.log 2>&1 </dev/null &
        echo "$(date '+%F %T') rescue: 隧道已拉起" >> /tmp/rescue.log
    fi
    sleep 20
done

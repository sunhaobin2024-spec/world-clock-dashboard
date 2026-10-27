#!/usr/bin/env bash
# 管理台守护：8443 拒连即拉起（每 30 秒检查）
cd /workspace/linux-sim
while true; do
  if ! curl -s -o /dev/null --max-time 4 http://127.0.0.1:8443/; then
    echo "[$(date +%T)] 8443 无响应，拉起 admin_server"
    nohup python3 admin_server.py >>/tmp/admin.log 2>&1 &
  fi
  sleep 30
done

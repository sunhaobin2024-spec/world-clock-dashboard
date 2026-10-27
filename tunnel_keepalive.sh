#!/bin/sh
# Named Tunnel 边缘连接保活：空闲超时会被 Cloudflare 边缘踢掉，
# 下次请求变成"冷连接"，首字节要多花一两秒（用户体感就是"加载慢"）。
# 每 10s 探活一次固定域名，保持连接温热；顺便巡检主站可达性。
#
# 孤儿清理：autostart 的兜底 quick 隧道（--url :8080 随机域名）只在
# 命名隧道完全起不来时才该存在。一旦命名隧道(http2)健康，就把它清掉，
# 避免双隧道冗余 + 随机域名噪声。判定"命名健康"用只有命名隧道才服务的
# system. 子域（quick 只代理 127.0.0.1:8080 根站，不服务子域）。
while true; do
  curl -s -o /dev/null --max-time 8 https://3920850391.dpdns.org/ 2>/dev/null

  # 命名隧道实例在？
  if ps -eo args --no-headers | grep -q "[c]loudflared tunnel --no-autoupdate --protocol http2"; then
    # 探测仅命名隧道才服务的子域，确认它真的在对外服务
    if curl -s -o /dev/null --max-time 8 https://system.3920850391.dpdns.org/ 2>/dev/null; then
      # 清掉所有孤儿 quick 隧道（--url :8080 的 account-less 随机域名实例）
      for pid in $(ps -eo pid,args --no-headers | grep "[c]loudflared tunnel --no-autoupdate --url" | awk '{print $1}'); do
        kill "$pid" 2>/dev/null && echo "$(date '+%F %T') 命名隧道已健康，清理孤儿 quick 隧道 PID $pid" >>/tmp/tunnel_keepalive.log
      done
    fi
  fi

  sleep 10
done

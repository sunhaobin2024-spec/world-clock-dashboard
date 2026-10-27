#!/bin/sh
# Named Tunnel 边缘连接保活：空闲超时会被 Cloudflare 边缘踢掉，
# 下次请求变成"冷连接"，首字节要多花一两秒（用户体感就是"加载慢"）。
# 每 10s 探活一次固定域名，保持连接温热；顺便巡检主站可达性。
while true; do
  curl -s -o /dev/null --max-time 8 https://3920850391.dpdns.org/ 2>/dev/null
  sleep 10
done

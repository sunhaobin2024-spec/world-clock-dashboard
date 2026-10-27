#!/usr/bin/env python3
# ============================================================
# 一键添加子域名（DNS + 隧道 ingress + 重启 cloudflared）
# 用法：python3 add_subdomain.py <子域前缀> <本地端口> [说明]
# 示例：python3 add_subdomain.py vnc 6080
#       python3 add_subdomain.py blog 8081
# 需要：Cloudflare API Token（Zone DNS Edit），存于 .cloudflare/token
# ============================================================
import sys, os, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ZONE = "c5effb7f5d10b98966dc0874cf6c1015"          # 3920850391.dpdns.org
DOMAIN = "3920850391.dpdns.org"
TUNNEL = "7ed54865-e16c-44fc-a03a-1708f9ae61d6.cfargotunnel.com"
TUNNEL_CFG = os.path.join(HERE, ".tunnel", "config.yml")
TOKEN_FILE = os.path.join(HERE, ".cloudflare", "token")

API = "https://api.cloudflare.com/client/v4"

def api(method, path, data=None):
    tok = open(TOKEN_FILE).read().strip()
    req = urllib.request.Request(API + path, method=method,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        data=json.dumps(data).encode() if data else None)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    sub, port = sys.argv[1].strip(), sys.argv[2].strip()
    host = f"{sub}.{DOMAIN}" if sub != "@" else DOMAIN

    # 1) DNS：已存在则跳过，否则创建（CNAME -> 隧道，开代理）
    existing = api("GET", f"/zones/{ZONE}/dns_records?type=CNAME&name={host}")["result"]
    if existing:
        print(f"[DNS] {host} 已存在，跳过（{existing[0]['content'][:40]}…）")
    else:
        r = api("POST", f"/zones/{ZONE}/dns_records", {
            "type": "CNAME", "name": host, "content": TUNNEL,
            "proxied": True, "ttl": 1, "comment": sys.argv[3] if len(sys.argv) > 3 else ""})
        print(f"[DNS] 已创建 {host} -> {TUNNEL[:40]}… 代理: {r['result']['proxied']}")

    # 2) ingress：config.yml 插入规则（已存在则跳过）
    cfg = open(TUNNEL_CFG).read()
    if f"hostname: {host}" in cfg:
        print("[ingress] config.yml 已有该子域，跳过")
    else:
        rule = f"  - hostname: {host}\n    service: http://127.0.0.1:{port}\n"
        head, sep, tail = cfg.partition("ingress:\n")
        assert sep, "config.yml 格式不对"
        open(TUNNEL_CFG, "w").write(head + sep + rule + tail)
        print(f"[ingress] 已加 {host} -> 127.0.0.1:{port}")

    # 3) 重启隧道（本地 ingress 模式）
    os.system("pkill -f '^/tmp/cloudflared tunnel'")
    time.sleep(2)
    os.system(f"cd {HERE} && nohup /tmp/cloudflared tunnel --no-autoupdate "
              f"--config .tunnel/config.yml run {TUNNEL.split('.')[0]} "
              f">>/tmp/cf-tunnel.log 2>&1 &")
    time.sleep(6)
    print("[tunnel] 已重启（本地 ingress 模式）")

    # 4) 验证
    import urllib.error, ssl
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(f"https://{host}/", timeout=15, context=ctx) as r:
            print(f"[验证] https://{host} -> HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"[验证] https://{host} -> HTTP {e.code}（隧道已通，端口后端未起属正常）")
    except Exception as e:
        print(f"[验证] 暂不可达（DNS 传播中，几分钟后自动生效）: {e}")

if __name__ == "__main__":
    main()

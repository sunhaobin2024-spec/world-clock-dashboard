#!/usr/bin/env bash
# BT 完成守护：live.iso 就位后把 kali-usb 条目升级为直接下载
cd /workspace/linux-sim
while [ -f "win/images/kali-linux-2026.2-live-amd64.iso.aria2" ] || ! pgrep aria2c >/dev/null; do
  pgrep aria2c >/dev/null || nohup aria2c -c --enable-dht=true --dht-entry-point=dht.transmissionbt.com:6969 --bt-max-peers=200 --seed-time=0 --listen-port=6881-6999 --bt-tracker="http://tracker.opentrackr.org:1337/announce,udp://tracker.opentrackr.org:1337/announce,https://bittorrent.kali.org/announce" -d win/images -o kali-linux-2026.2-live-amd64.iso /tmp/kalit/live.torrent >>/tmp/kali_live_bt.log 2>&1 &
  sleep 60
done
python3 - <<'PY'
import json
p="store/catalog.json"
d=json.load(open(p))
items=d if isinstance(d,list) else d.get("items")
for it in items:
    if it.get("id")=="kali-usb":
        it["size"]="5,276 MB"
        it["desc"]="官方 Live 版：写入 U 盘即用（Ventoy/Rufus 可引导、支持持久化）。完整 ISO 已入库，直接下载。amd64 64 位，用于真实 U 盘；v86 网页虚拟机仅支持 32 位，无法运行它。"
        it["src"]="/win/images/kali-linux-2026.2-live-amd64.iso"
        it["fname"]="kali-linux-2026.2-live-amd64.iso"
        print("kali-usb 已升级为 Live ISO 直下")
json.dump(d, open(p,"w"), ensure_ascii=False, indent=2)
PY
echo "[$(date +%T)] BT Live 全部完成"

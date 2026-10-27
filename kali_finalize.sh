#!/usr/bin/env bash
# Kali installer ISO 下载守护：断线自动续传，下完自动上架商店条目
cd /workspace/linux-sim
TARGET=4802531328
PART="win/images/kali-linux-2026.2-installer-amd64.iso.part"
FINAL="win/images/kali-linux-2026.2-installer-amd64.iso"
URL="https://cdimage.kali.org/kali-2026.2/kali-linux-2026.2-installer-amd64.iso"

while true; do
  sz=$(stat -c %s "$PART" 2>/dev/null || echo 0)
  if [ "$sz" -ge "$TARGET" ]; then break; fi
  if ! pgrep -f "cdimage.kali.org/kali-2026.2/kali-linux-2026.2-installer" >/dev/null; then
    echo "[$(date +%T)] 下载进程不在（已下 $sz），续传…"
    nohup curl -sL -C - -o "$PART" --max-time 3600 "$URL" >/tmp/kali_dl.log 2>&1 &
  fi
  sleep 45
done

mv "$PART" "$FINAL"
echo "[$(date +%T)] 下载完成，上架 kali-iso 条目"
python3 - <<'PY'
import json
p="store/catalog.json"
d=json.load(open(p))
items=d if isinstance(d,list) else d.get("items")
if not any(i.get("id")=="kali-iso" for i in items):
    items.append({"id":"kali-iso","name":"Kali Linux 2026.2 安装 ISO","icon":"🐉",
        "category":"系统镜像","size":"4,802 MB",
        "desc":"官方安装版 ISO：刻录 U 盘/DVD 后安装到硬盘。amd64 64 位，用于真实机器；v86 网页虚拟机仅支持 32 位，无法运行它。",
        "src":"/win/images/kali-linux-2026.2-installer-amd64.iso",
        "fname":"kali-linux-2026.2-installer-amd64.iso"})
    json.dump(d, open(p,"w"), ensure_ascii=False, indent=2)
    print("kali-iso 已上架")
PY
echo "[$(date +%T)] 全部完成"

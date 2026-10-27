#!/bin/bash
exec 9>/tmp/vnc.lock
flock -n 9 || exit 0   # 已有实例在跑则立即退出，避免 rescue 并发重复拉起 websockify
# noVNC 远程桌面栈：Xvfb(虚拟显示) + XFCE(完整图形界面) + x11vnc(抓屏) + websockify(转 websocket)
# 幂等：每个组件"已在跑就跳过、没跑才拉起"，被反复调用也不会拆掉健康进程。
export DISPLAY=:1
XVFB=/usr/bin/Xvfb
X11VNC=/usr/bin/x11vnc
WEBSOCKIFY=/usr/bin/websockify
NOVNC=/usr/share/novnc
VNC_PASS="575006"   # VNC(RFB) 密码最长 8 位，与网站通用口令一致

running() { pgrep -f "$1" >/dev/null 2>&1; }

# 1) 虚拟显示 :1 (1280x720)
if ! running "Xvfb :1"; then
  $XVFB :1 -screen 0 1280x720x24 -ac +extension RANDR +extension GLX +extension RENDER >/tmp/xvfb.log 2>&1 9>&- &
  sleep 2
fi

# 2) 桌面环境 XFCE（壁纸 / 任务栏 / 开始菜单，真正的图形界面）
if ! running "xfce4-session"; then
  startxfce4 >/tmp/xfce4.log 2>&1 9>&- &
  sleep 6
fi

# 3) 设壁纸（XFCE 默认就不是灰；再换成统一壁纸）
DISPLAY=:1 xfconf-query -c xfce4-desktop \
  -p /backdrop/screen0/monitor0/workspace0/last-image -n -t string -s /root/.wallpaper.png >/dev/null 2>&1
DISPLAY=:1 feh --bg-fill /root/.wallpaper.png >/dev/null 2>&1

# 4) x11vnc 抓 :1，设密码（只用 -passwd 明文，避免与 -rfbauth 冲突）
if ! running "x11vnc"; then
  $X11VNC -display :1 -passwd "$VNC_PASS" -forever -shared -nolookup -noipv6 -rfbport 5901 >/tmp/x11vnc.log 2>&1 9>&- &
  sleep 2
fi

# 5) websockify: 6080 -> 5901，挂载 noVNC web
# 按"端口 6080 是否在监听"判断，而不是匹配 websockify 关键字（那样会和 8081 那条互相干扰且重复拉起）。
if ! (ss -ltn 2>/dev/null | grep -qE '[:.]6080[[:space:]]'); then
  $WEBSOCKIFY --web $NOVNC 6080 localhost:5901 >/tmp/websockify.log 2>&1 9>&- &
  sleep 2
fi

# 6) 预开终端（xfce4-terminal，仅在没有任何时开，避免叠加）
if ! running "xfce4-terminal"; then
  xfce4-terminal --title="终端-操作" >/tmp/xfce-term1.log 2>&1 9>&- &
  xfce4-terminal --title="系统监控-htop" -e htop >/tmp/xfce-term2.log 2>&1 9>&- &
fi
echo "VNC_STACK_CHECKED display=:1 vncport=5901 web=6080 (XFCE)"

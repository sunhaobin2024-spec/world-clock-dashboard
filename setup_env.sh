#!/bin/bash
# 让沙箱真正成为一台 Ubuntu 桌面：真实用户名 user / 主机名 ubuntu / 桌面目录
#
# 安全说明：这里刻意不给 user 免密 sudo。/api/exec 是公网可达的命令执行入口，
# 一旦 user 能免密 sudo，任何拿到访问口令的人都能直接提权到 root，
# runuser 降权就完全失去意义。需要装包时由后端进程（root）代劳。
set +e
hostname ubuntu 2>/dev/null
grep -q "127.0.0.1 ubuntu" /etc/hosts 2>/dev/null || echo "127.0.0.1 ubuntu" >> /etc/hosts
grep -q "127.0.0.1 ubuntu" "$HOME/.user_hosts" 2>/dev/null || echo "127.0.0.1 ubuntu" >> "$HOME/.user_hosts"
id user >/dev/null 2>&1 || useradd -m -s /bin/bash user
# 移除 sudo 组与免密规则（重复执行无害，保证幂等）
rm -f /etc/sudoers.d/user 2>/dev/null
gpasswd -d user sudo >/dev/null 2>&1
runuser -u user -- mkdir -p /home/user/桌面 /home/user/下载 /home/user/文档 /home/user/图片 /home/user/音乐 /home/user/视频 2>/dev/null
runuser -u user -- bash --noprofile --norc -c 'cd ~ && [ -f ~/.bashrc ] || cp /etc/skel/.bashrc ~/ 2>/dev/null' 2>/dev/null
# 让终端里的 user 也能访问「我的文件」上传目录
mkdir -p /workspace/linux-sim/userfiles 2>/dev/null
chmod 777 /workspace/linux-sim/userfiles 2>/dev/null
# 首次启动更新软件源索引，保证后端（root）能装包
if ! ls /var/lib/apt/lists/*Packages* >/dev/null 2>&1; then
  timeout 90 apt-get update >/dev/null 2>&1
fi
echo "[setup_env] host=$(hostname) user=$(id -un user 2>/dev/null) ok"


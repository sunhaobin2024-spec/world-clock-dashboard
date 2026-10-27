#!/bin/bash
# linux-sim 后端守护：崩溃自动拉起 + 健康检查
#
# 2026-10-03 起主守护交给 guardian.py（Python），本脚本只当启动器/兼容入口。
# 原因：原来这个 bash 版只会 `ss -ltn | grep 8080`，也就是"端口还在不在"。
# 端口在 ≠ 服务能用 —— 线程池占满、内存泄漏到上限、cloudflared 挂了、
# 隧道 URL 悄悄换了，这四种情况它一个都发现不了。其中隧道那条最坑：
# 后端日志一切正常、公网也能通，但你手机里存的链接已经打不开了。
#
# 用法：
#   ./watchdog.sh start    启动守护
#   ./watchdog.sh stop     停止
#   ./watchdog.sh status   查看状态
#   ./watchdog.sh log      看后端日志
#   ./watchdog.sh guard    只看守护日志
#   ./watchdog.sh url      打印当前公网地址

HERE="$(cd "$(dirname "$0")" && pwd)"
SELF="$HERE/$(basename "$0")"   # 绝对路径，setsid 起来的子进程换个 cwd 也能找到
PORT=8080
PIDFILE=/tmp/linuxsim.pid
LOG=/tmp/linuxsim-watchdog.log
SRVLOG=/tmp/srv.log
# 永久密钥放在项目里，chmod 600。/tmp 会被清空，存那里重启就换口令了。
KEYFILE=/workspace/linux-sim/.access_keys
INTERVAL=10

# 口令要跨重启保持一致，否则用户存的链接会失效
# 口令固定不变：守护重启 / 崩溃拉起后仍是同一个，你存的链接不会失效。
if [ ! -f "$KEYFILE" ]; then
  # 一次生成两个：主密钥（自己用）+ 备用密钥（对外测试）。
  # 都避开 0/O/1/l/I，手动抄写最不容易错的就是这几个。
  python3.11 -c '
import secrets
d="23456789"; a="abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
mk="".join(secrets.choice(d) for _ in range(6))
tk="".join(secrets.choice(a) for _ in range(6))
print("# linux-sim 访问密钥（每行一个，第一个是主密钥）
# 权限 600，不要提交到 git")
print(mk); print("# 备用密钥（测试/分享用）"); print(tk)' > "$KEYFILE"
  chmod 600 "$KEYFILE"
fi
# 取第一个非注释行当主密钥；后端会读整个文件，所以备用密钥也自动生效
KEY=$(grep -v "^#" "$KEYFILE" | grep -v "^$" | head -1)

alive(){ ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT"; }
# 守护进程在不在。guardian.py 才是主守护，这里只判断它活着没。
guard_alive(){ pgrep -f "guardian\.py" >/dev/null 2>&1; }

boot_once(){
  cd "$HERE" || exit 1
  LINUXSIM_KEY="$KEY" setsid nohup python3.11 server.py >> "$SRVLOG" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  for i in $(seq 1 20); do
    sleep 1
    alive && { echo "[$(date '+%F %T')] 后端已启动 pid=$(cat $PIDFILE)" >> "$LOG"; return 0; }
  done
  echo "[$(date '+%F %T')] 启动失败，日志尾部：" >> "$LOG"
  tail -5 "$SRVLOG" >> "$LOG"
  return 1
}

case "$1" in
  start)
    if alive; then
      W=$(cat /tmp/linuxsim-watch.pid 2>/dev/null)
      if guard_alive && [ -n "$W" ] && kill -0 "$W" 2>/dev/null; then
        echo "已在运行（127.0.0.1:$PORT），守护 pid=$W"; exit 0
      fi
    else
      boot_once || { echo "启动失败"; exit 1; }
    fi
    # 拉起真正的守护。guardian.py 自己管后端 + 隧道 + 内存 + 文件完整性，
    # 比原来那个只探端口的 bash 循环覆盖面大得多。
    setsid nohup python3.11 "$HERE/guardian.py" >/dev/null 2>&1 < /dev/null &
    # 等它自己写完 pid 文件，不要用 sleep+pgrep 去猜 ——
    # sleep 期间新守护可能还没起来，pgrep 会返回空，误判成"没起来"。
    # 更坑的是：上一版用 pgrep 找自己，把刚启动的守护连同调用方一起杀了
    # （命令行里同样含 guardian.py 字样），表现为 start 之后守护秒退。
    W=""
    for i in $(seq 1 20); do
      W=$(pgrep -f "guardian\.py" 2>/dev/null | head -1)
      [ -n "$W" ] && break
      sleep 0.5
    done
    if [ -n "$W" ]; then
      echo "$W" > /tmp/linuxsim-watch.pid
      echo "守护已启动（pid $W），每 ${INTERVAL}s 巡检：后端/隧道/内存/密钥/入口"
    else
      echo "警告：后端已起，但守护进程没起来，崩溃将不会自动恢复"
    fi
    U=$(cat "$HERE/.public_url" 2>/dev/null)
    [ -n "$U" ] && echo "公网地址: $U"
    echo "访问口令: $KEY"; exit 0 ;;
  stop)
    P=$(ss -ltnp 2>/dev/null | grep "127.0.0.1:$PORT" | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$P" ] && kill "$P" 2>/dev/null
    # 停守护按 pid 文件精确杀，不用 pgrep/pkill。
    # 原因：pgrep -f "guardian.py" 会把发起 stop 的这个 shell 自己也匹配上
    #（命令行里同样含 guardian.py 字样），杀完自己进程就没了，
    # 后面的清理动作全执行不到。踩过一次，现象是 start 之后守护秒退。
    W=$(cat /tmp/linuxsim-watch.pid 2>/dev/null)
    if [ -n "$W" ] && kill -0 "$W" 2>/dev/null; then
      kill "$W" 2>/dev/null
    fi
    # 兜底：pid 文件丢了/过期时，只杀 python3.11 明确执行的守护进程
    for G in $(pgrep -x python3.11 2>/dev/null); do
      grep -q "guardian.py" /proc/$G/cmdline 2>/dev/null && kill "$G" 2>/dev/null
    done
    sleep 1
    rm -f "$PIDFILE" /tmp/linuxsim-watch.pid
    echo "已停止（隧道保持运行，重启守护不会换网址）"; exit 0 ;;
  restart)
    # 必须用绝对路径：$0 在 `bash watchdog.sh` 这种调法下是相对路径，
    # 换到别的 cwd 就找不到脚本了（之前报 "command not found" 就是这原因）
    "$SELF" stop; sleep 2; "$SELF" start; exit $? ;;
  status)
    if alive; then
      P=$(ss -ltnp 2>/dev/null | grep "127.0.0.1:$PORT" | grep -oP 'pid=\K[0-9]+' | head -1)
      RSS=$(awk '/VmRSS/{print $2}' /proc/$P/status 2>/dev/null)
      echo "后端    运行中  pid=$P  内存 $((RSS/1024))MB"
    else
      echo "后端    未运行"
    fi
    CF=$(pgrep -f "cloudflared tunnel" 2>/dev/null | head -1)
    if [ -n "$CF" ]; then echo "隧道    运行中  pid=$CF"
    else echo "隧道    未运行"; fi
    W=$(pgrep -f "guardian\.py" 2>/dev/null | head -1)
    if [ -n "$W" ]; then echo "守护    运行中  pid=$W"
    else echo "守护    未运行  ← 崩溃不会自动恢复"; fi
    U=$(cat "$HERE/.public_url" 2>/dev/null)
    [ -n "$U" ] && echo "公网    $U"
    echo "--- 守护最近日志 ---"; tail -6 "$HERE/.guardian.log" 2>/dev/null
    echo "--- 后端最近日志 ---"; tail -5 "$SRVLOG" 2>/dev/null
    exit 0 ;;
  log) tail -30 "$SRVLOG"; exit 0 ;;
  guard) tail -40 "$HERE/.guardian.log" 2>/dev/null; exit 0 ;;
  url) cat "$HERE/.public_url" 2>/dev/null || echo "（守护还没记录公网地址）"; exit 0 ;;
  watch)
    # 兼容旧调用：真正的巡检在 guardian.py 里，这里只提示别用这个
    echo "巡检逻辑已移到 guardian.py，请执行：$SELF start"; exit 1 ;;
  *) echo "用法: $0 {start|stop|restart|status|log|guard|url}"; exit 1 ;;
esac

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin_chat_monitor.py —— 管理后台「文档式对话」大小轮询监测

机制（按用户要求）：每 ADMIN_CHAT_INTERVAL 秒（默认 60 = 1 分钟）比对一次
feedback/admin_chat_doc.txt 的文件大小：
  - 大小没变 → 无事发生
  - 变大了（说明用户在文档里写了新内容） → 读出新增部分 → 提取用户消息
    → 把「🤖 助手」的回答写回文档（紧跟在用户消息下面）

回答来源：
  1) 若配置了 LLM（见下方环境变量）→ 自动调用并写回回答（无人值守，真正 1 分钟回）
  2) 未配置 LLM → 只做「检测+记录待处理」(写 admin_chat_pending.txt + 日志)，
     由管家（WorkBuddy 代理）在下次对话时把回答写进文档

文档即聊天记录本身，管理后台网页聊天框也读写同一个文档（历史已迁移）。

LLM 自动回复（可选，按需开启）：
  ADMIN_CHAT_LLM_URL   如 https://open.bigmodel.cn/api/paas/v4/chat/completions
  ADMIN_CHAT_LLM_KEY   你的 API Key
  ADMIN_CHAT_LLM_MODEL 模型名（如 glm-4-flash），默认留空时用 URL 默认
"""
import os, sys, time, json, fcntl, urllib.request, urllib.error

HERE = "/workspace/linux-sim"
FB_DIR = os.path.join(HERE, "feedback")
DOC_FILE = os.path.join(FB_DIR, "admin_chat_doc.txt")
SIZE_FILE = os.path.join(FB_DIR, ".admin_chat_doc_size")
PENDING_FILE = os.path.join(FB_DIR, "admin_chat_pending.txt")
LOG_FILE = os.path.join(FB_DIR, "admin_chat_monitor_log.txt")
SCAN_FILE = os.path.join(FB_DIR, "admin_chat_last_scan.json")

INTERVAL = int(os.environ.get("ADMIN_CHAT_INTERVAL", "60"))
LLM_URL = os.environ.get("ADMIN_CHAT_LLM_URL", "")
LLM_KEY = os.environ.get("ADMIN_CHAT_LLM_KEY", "")
LLM_MODEL = os.environ.get("ADMIN_CHAT_LLM_MODEL", "")


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(line + "\n")
    except Exception:
        pass


def load_size():
    try:
        return int(open(SIZE_FILE).read().strip())
    except Exception:
        return 0


def save_size(n):
    try:
        open(SIZE_FILE, "w").write(str(n))
    except Exception:
        pass


def cur_size():
    try:
        return os.path.getsize(DOC_FILE)
    except Exception:
        return 0


def parse_user_msgs(region):
    """从新增片段里提取用户消息：所有不以 🤖 开头的非空行（🤖 是我的回答，跳过）。"""
    msgs = []
    cur = []
    for ln in region.split("\n"):
        s = ln.strip()
        if not s:
            if cur:
                msgs.append("\n".join(cur).strip())
                cur = []
            continue
        if s.startswith("🤖"):
            if cur:
                msgs.append("\n".join(cur).strip())
                cur = []
            continue
        cur.append(s)
    if cur:
        msgs.append("\n".join(cur).strip())
    return [m for m in msgs if m]


def parse_doc_history():
    """整文档解析成 [{role,ts,text}]，给 LLM 当上下文。"""
    recs = []
    cur_role = None
    cur_ts = ""
    cur_txt = []
    with open(DOC_FILE, encoding="utf-8") as fh:
        for ln in fh:
            if ln.startswith("👤") or ln.startswith("🤖"):
                if cur_role:
                    recs.append({"role": cur_role, "ts": cur_ts,
                                 "text": "\n".join(cur_txt).strip()})
                cur_role = "user" if ln.startswith("👤") else "assistant"
                rest = ln.split("：", 1)
                cur_ts = (rest[0].replace("👤 用户", "")
                          .replace("🤖 助手", "").strip())
                cur_txt = [rest[1].strip()] if len(rest) > 1 else []
            else:
                if cur_role is not None:
                    cur_txt.append(ln.rstrip("\n"))
    if cur_role:
        recs.append({"role": cur_role, "ts": cur_ts,
                     "text": "\n".join(cur_txt).strip()})
    return recs


def append_assistant(text):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    block = f"\n🤖 助手 {ts}：{text}\n"
    with open(DOC_FILE, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(block)
    return block


def llm_reply(history):
    if not (LLM_URL and LLM_KEY):
        return None
    msgs = [{"role": "system",
             "content": "你是 cloudsystem 管理后台的运维助手，回答简洁、直接、中文。"}]
    for r in history[-12:]:
        msgs.append({"role": "user" if r.get("role") == "user" else "assistant",
                     "content": r.get("text", "")})
    body = {"messages": msgs}
    if LLM_MODEL:
        body["model"] = LLM_MODEL
    req = urllib.request.Request(
        LLM_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + LLM_KEY,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        return (data.get("choices", [{}])[0].get("message", {}).get("text")
                or data.get("output") or "").strip() or None
    except Exception as e:
        log(f"LLM 自动回复失败（留待人工）：{e}")
        return None


def render_pending(new):
    lines = []
    lines.append("=" * 56)
    lines.append("文档式对话 · 待管家回复（每次巡检刷新）")
    lines.append("生成时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("本次新增：" + str(len(new)))
    lines.append("=" * 56)
    if not new:
        lines.append("（暂无新消息）")
    else:
        for i, m in enumerate(new, 1):
            lines.append(f"\n[{i}] {m[:200]}")
    lines.append("\n" + "=" * 56)
    return "\n".join(lines)


def scan(first_run=False):
    size = cur_size()
    last = load_size()
    if size < last:           # 文档被改小/重置，重新基线
        last = 0
    if size <= last and not first_run:
        return {"last_scan": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total": len(parse_doc_history()), "new_this_scan": 0,
                "llm_auto_reply": bool(LLM_URL and LLM_KEY)}
    # 读新增片段
    region = ""
    if size > last:
        try:
            with open(DOC_FILE, encoding="utf-8") as fh:
                fh.seek(last)
                region = fh.read()
        except Exception:
            region = ""
    user_msgs = parse_user_msgs(region) if region else []
    answered = 0
    if user_msgs and not first_run:
        hist = parse_doc_history()
        for m in user_msgs:
            log(f"💬 检测到新消息 | {m[:80]}")
            ans = llm_reply(hist) if (LLM_URL and LLM_KEY) else None
            if ans:
                append_assistant(ans)
                answered += 1
                log(f"🤖 已自动回复（LLM）：{ans[:60]}")
            else:
                log(f"⏳ 已记录待回复（无 LLM 密钥，留待管家）")
    if first_run:
        log(f"初次巡检：建立基线（文档 {size} 字节，不报警）")
    # 写完回答后文件可能变大，重新取末位
    save_size(cur_size())
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as fh:
            fh.write(render_pending(user_msgs if (user_msgs and not first_run) else []))
    except Exception:
        pass
    summary = {
        "last_scan": time.strftime("%Y-%m-%d %H:%M:%S"),
        "doc_bytes": cur_size(),
        "new_this_scan": len(user_msgs) if (user_msgs and not first_run) else 0,
        "auto_answered": answered,
        "llm_auto_reply": bool(LLM_URL and LLM_KEY),
    }
    try:
        with open(SCAN_FILE, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    if user_msgs and not first_run:
        log(f"巡检完成 新增={summary['new_this_scan']} 自动回={answered} "
            f"自动回复={'开' if summary['llm_auto_reply'] else '关'}")
    return summary


def main():
    os.makedirs(FB_DIR, exist_ok=True)
    if os.environ.get("ONE_SHOT"):
        scan(first_run=False)
        return
    log(f"admin_chat_monitor 启动（文档大小轮询），间隔 {INTERVAL}s"
        f"{' [LLM自动回复已开]' if (LLM_URL and LLM_KEY) else ' [未配LLM：仅检测+待回复]'}")
    try:
        scan(first_run=True)
    except Exception as e:
        log(f"首次巡检异常：{e}")
    while True:
        try:
            time.sleep(INTERVAL)
            scan(first_run=False)
        except Exception as e:
            log(f"巡检异常（继续重试）：{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feedback_monitor.py —— 网站用户建议 / 问题反馈 定时巡检守护

每 FB_INTERVAL 秒（默认 600 = 10 分钟）扫描一次 feedback/feedback.jsonl：
  1. 发现「新提交」→ 写入带时间戳的 monitor_log.txt（含类型/标题/内容/来源）
  2. 重新生成 pending.txt（所有 fixed!=true 的待处理建议，供维护方随时看）
  3. 写出 last_scan.json 摘要（总数/本次新增/待处理数/最近一次时间戳）
  4. 向 stdout 打一行心跳，便于配合保活机制

设计要点：
  - 只读 feedback.jsonl（服务端 server.py 用 flock 独占追加，互不冲突）
  - seen 状态存 feedback/.seen_ids.json，重启后不重复报警
  - 首次运行会把已存在且未修复的建议写入 pending.txt，但不刷"新提交"日志
  - 用 setsid + nohup 脱离终端常驻；脚本自身做成可自愈的 while 循环
"""
import os, sys, json, time, fcntl

HERE = "/workspace/linux-sim"
FB_DIR = os.path.join(HERE, "feedback")
FB_FILE = os.path.join(FB_DIR, "feedback.jsonl")
SEEN_FILE = os.path.join(FB_DIR, ".seen_ids.json")
LOG_FILE = os.path.join(FB_DIR, "monitor_log.txt")
PENDING_FILE = os.path.join(FB_DIR, "pending.txt")
SCAN_FILE = os.path.join(FB_DIR, "last_scan.json")

INTERVAL = int(os.environ.get("FB_INTERVAL", "60"))


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


def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    except Exception:
        return set()


def save_seen(seen):
    try:
        tmp = SEEN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh, ensure_ascii=False)
        os.replace(tmp, SEEN_FILE)
    except Exception:
        pass


def load_records():
    recs = []
    if not os.path.exists(FB_FILE):
        return recs
    with open(FB_FILE, "r", encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                recs.append(json.loads(raw))
            except Exception:
                # 坏行跳过，不影响整体
                continue
    return recs


def source_tag(rec):
    ua = (rec.get("ua") or "").lower()
    if "android" in ua or "iphone" in ua or "mobile" in ua:
        return "移动端"
    if "windows" in ua:
        return "Windows"
    if "mac" in ua:
        return "Mac"
    return "其他"


def render_pending(recs):
    pending = [r for r in recs if not r.get("fixed")]
    lines = []
    lines.append("=" * 56)
    lines.append("待处理建议清单（每次巡检自动刷新）")
    lines.append("生成时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("未修复总数：" + str(len(pending)))
    lines.append("=" * 56)
    if not pending:
        lines.append("（暂无待处理建议，棒）")
    else:
        for i, r in enumerate(pending, 1):
            lines.append(f"\n[{i}] {r.get('id','?')}  [{r.get('type','其他')}]")
            lines.append(f"    标题：{r.get('title','')}")
            lines.append(f"    内容：{r.get('detail','')}")
            if r.get("contact"):
                lines.append(f"    联系方式：{r.get('contact')}")
            if r.get("url"):
                lines.append(f"    来源页：{r.get('url')}")
            lines.append(f"    提交时间：{r.get('ts','')}  | 来源：{source_tag(r)}")
    lines.append("\n" + "=" * 56)
    return "\n".join(lines)


def scan(first_run=False):
    recs = load_records()
    seen = load_seen()
    new = []
    for r in recs:
        rid = r.get("id")
        if rid and rid not in seen:
            new.append(r)
    # 记录新提交
    if new and not first_run:
        for r in new:
            log(f"📥 新建议 {r.get('id','?')} | 类型:{r.get('type','其他')} | "
                f"标题:{r.get('title','')} | 内容:{r.get('detail','')[:80]} | "
                f"来源:{source_tag(r)}")
    elif new and first_run:
        log(f"初次巡检：发现 {len(new)} 条已有建议（已纳入待处理清单，不重复报警）")
    # 更新 seen
    for r in recs:
        if r.get("id"):
            seen.add(r["id"])
    save_seen(seen)
    # 刷新待处理清单 + 摘要
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as fh:
            fh.write(render_pending(recs))
    except Exception:
        pass
    pending_count = sum(1 for r in recs if not r.get("fixed"))
    summary = {
        "last_scan": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(recs),
        "new_this_scan": len(new) if not first_run else 0,
        "pending": pending_count,
        "last_suggestion_ts": recs[-1].get("ts") if recs else None,
    }
    try:
        with open(SCAN_FILE, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    log(f"巡检完成 总计={len(recs)} 本次新增={summary['new_this_scan']} 待处理={pending_count}")
    return summary


def main():
    os.makedirs(FB_DIR, exist_ok=True)
    # 单次扫描模式：手动/cron 触发，跑一次即退出（用于测试或外部调度）
    if os.environ.get("ONE_SHOT"):
        scan(first_run=False)
        return
    # 第一次运行：仅初始化 seen 与 pending，不刷"新提交"日志
    log(f"feedback_monitor 启动，间隔 {INTERVAL}s（首次巡检仅建基线）")
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

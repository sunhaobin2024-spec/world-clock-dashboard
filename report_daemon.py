#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""上线 IP 日报守护：每隔一天（86400s）把 上线IP日志.txt 汇总成一份报告，
落到 /workspace/每天IP报告_YYYY-MM-DD.txt。

若配置了推送钩子（环境变量 PUSH_URL 或 IP_REPORT_PUSH，企业微信/钉钉/飞书
webhook 均可，文本以 text= 表单字段 POST），则同时把报告 POST 出去；
未配置则只落盘，等用户自己来取。
"""
import os
import time
import subprocess

LOG = "/workspace/linux-sim/上线IP日志.txt"
OUTDIR = "/workspace"
INTERVAL = 24 * 3600  # 每隔一天


def gen_report():
    day = time.strftime("%Y-%m-%d")
    out = os.path.join(OUTDIR, f"每天IP报告_{day}.txt")
    cutoff = time.time() - INTERVAL
    rows = []
    try:
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 2:
                    continue
                try:
                    t = time.mktime(time.strptime(p[0], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    t = 0
                rows.append((t, p[0], p[1],
                             p[2] if len(p) > 2 else "",
                             p[3] if len(p) > 3 else "",
                             p[4] if len(p) > 4 else ""))
    except FileNotFoundError:
        rows = []

    recent = [r for r in rows if r[0] >= cutoff]
    agg = {}
    for r in recent:
        a = agg.setdefault(r[2], {"first": r[1], "last": r[1], "n": 0, "ua": r[5]})
        a["n"] += 1
        if r[5] and not a["ua"]:
            a["ua"] = r[5]
        try:
            if r[0] > time.mktime(time.strptime(a["last"], "%Y-%m-%d %H:%M:%S")):
                a["last"] = r[1]
        except Exception:
            pass

    lines = []
    lines.append(f"上线 IP 日报  {day}")
    lines.append(f"统计窗口：过去 24 小时（自 "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cutoff))} 起）")
    lines.append(f"总请求数：{len(recent)}    去重 IP 数：{len(agg)}")
    lines.append("=" * 64)
    for ip, a in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        lines.append(f"{ip}\t请求 {a['n']} 次\t首次 {a['first']}\t末次 {a['last']}\tUA {a['ua'][:60]}")
    lines.append("")
    lines.append("（本报告由 report_daemon 自动生成；配置 PUSH_URL 后自动推送到手机）")
    text = "\n".join(lines)

    with open(out, "w", encoding="utf-8") as f:
        f.write(text)

    push = os.environ.get("PUSH_URL") or os.environ.get("IP_REPORT_PUSH")
    if push:
        try:
            subprocess.run(["curl", "-s", "-m", "15", "-X", "POST", push,
                            "--data-urlencode", f"text={text}"], check=False)
        except Exception:
            pass
    return out, len(recent), len(agg)


def main():
    try:
        gen_report()
    except Exception:
        pass
    while True:
        time.sleep(INTERVAL)
        try:
            gen_report()
        except Exception:
            pass


if __name__ == "__main__":
    main()

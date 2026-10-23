#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py — 校验 Agent 输出是否符合框架要求

用法:
    python validate.py <report.md> [--role expert|red-team|aggressive|conservative]
    python validate.py --brief <context-brief.md>

检查项（对应 docs/ANTI-PATTERNS.md 里的常见失败）：
  1. 必备章节是否齐全
  2. 是否标注证据强度
  3. "不确定的地方"是否非空
  4. 是否出现"没有找到证据"这类诚实表述（缺失则可能是在编）
  5. 是否有命令级步骤（专家/激进派）
  6. 是否有定量指标（激进派/稳健派）
"""
import sys
import re
import argparse


REQUIRED = {
    "expert": [
        r"结论摘要",
        r"关键证据",
        r"推荐路线",
        r"不确定",
    ],
    "red-team": [
        r"事实\s*vs\s*推测|事实与推测",
        r"矛盾",
        r"其他解释|别的解释",
        r"遗漏",
        r"最可能的真相",
        r"如果只能做一件事",
    ],
    "aggressive": [
        r"天花板",
        r"行动清单",
        r"成功概率",
        r"最终预测|性能预测",
        r"如果只能做三件事|三件事",
    ],
    "conservative": [
        r"红线",
        r"零风险",
        r"重启",
        r"定量|成本|停机",
        r"最终建议|继续|收手",
    ],
}


def check(text, role):
    print("=" * 60)
    print("角色: %s" % role)
    print("文件大小: %d 字符" % len(text))
    print("=" * 60)

    ok = True
    pats = REQUIRED.get(role, [])

    print("\n[1] 必备章节")
    for p in pats:
        found = re.search(p, text) is not None
        print("  %s %s" % ("✅" if found else "❌", p))
        if not found:
            ok = False

    print("\n[2] 证据强度标注")
    n_strong = len(re.findall(r"证据强度|\[强\]|强\(|【强】", text))
    levels = sum(len(re.findall(k, text)) for k in ["强", "中", "弱"])
    print("  证据强度相关词出现: %d 次" % n_strong)
    if n_strong == 0:
        print("  ⚠️  未标注证据强度（不一定是错，但降低可信度）")

    print("\n[3] 诚实表述检查")
    honest = sum(len(re.findall(k, text)) for k in
                 ["没有找到证据", "未找到", "没找到", "无法判断", "不确定", "未知"])
    print("  '没有找到/无法判断/不确定' 类表述: %d 次" % honest)
    if honest == 0:
        print("  ❌ 一次都没有 → 高度可疑（很可能在编，或者没深入）")
        ok = False
    else:
        print("  ✅ 有诚实表述")

    print("\n[4] '不确定的地方'是否非空")
    m = re.search(r"不确定[^\n]*\n+((?:.|\n){0,400})", text)
    if m:
        body = m.group(1).strip()
        # 看这一段后面是否有实质内容（列表项）
        has_items = bool(re.search(r"^\s*[-*\d]", body, re.M))
        print("  %s" % ("✅ 有内容" if has_items else "❌ 可能是空的"))
        if not has_items:
            ok = False
    else:
        print("  ⚠️  未找到该章节")

    print("\n[5] 命令级步骤")
    cmds = len(re.findall(r"```(?:bash|sh|shell)?\n|^\s*[$#]\s+\w|`[a-z][\w\-]+\s+-", text, re.M))
    print("  疑似命令块/命令: %d 处" % cmds)
    if role in ("expert", "aggressive") and cmds == 0:
        print("  ❌ 该角色要求命令级步骤")
        ok = False

    print("\n[6] 定量指标")
    pcts = len(re.findall(r"\d+\s*%", text))
    nums = len(re.findall(r"\d+\.\d+", text))
    print("  百分比: %d 处   小数: %d 处" % (pcts, nums))
    if role in ("aggressive", "conservative") and pcts == 0:
        print("  ⚠️  该角色应给出定量指标")

    print("\n" + "=" * 60)
    print("结论: %s" % ("✅ 通过" if ok else "❌ 不通过（见上方 ❌ 项）"))
    print("=" * 60)
    return ok


def check_brief(text):
    print("=" * 60)
    print("Context Brief 校验")
    print("=" * 60)
    checks = [
        (r"目标", "目标章节"),
        (r"约束", "硬约束章节"),
        (r"已确证|事实", "事实章节"),
        (r"已排除|否证", "已排除路径"),
        (r"不知道|未知|不确定", "未知清单"),
    ]
    ok = True
    for pat, label in checks:
        found = re.search(pat, text) is not None
        print("  %s %s" % ("✅" if found else "❌", label))
        if not found:
            ok = False

    print("\n[反模式检查]")
    bad = [r"我认为", r"应该是", r"大概是"]
    for p in bad:
        n = len(re.findall(p, text))
        if n:
            print("  ⚠️  出现「%s」%d 次（可能塞入了倾向性）" % (p, n))

    print("\n" + "=" * 60)
    print("结论: %s" % ("✅ 通过" if ok else "❌ 不通过"))
    print("=" * 60)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="待检查的 markdown 文件")
    ap.add_argument("--role", default="expert",
                    choices=list(REQUIRED.keys()) + ["brief"])
    ap.add_argument("--brief", action="store_true", help="按 Context Brief 规则检查")
    args = ap.parse_args()

    if not args.file:
        ap.print_help()
        return 1

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print("文件不存在: %s" % args.file)
        return 1

    if args.brief or args.role == "brief":
        ok = check_brief(text)
    else:
        ok = check(text, args.role)

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

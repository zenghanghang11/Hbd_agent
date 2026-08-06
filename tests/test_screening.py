# -*- coding: utf-8 -*-
"""筛查逻辑回归测试: python3 tests/test_screening.py"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import hk_address as hk
import screen_bad_cases as sc

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append("%s\n    期望: %r\n    实际: %r" % (name, want, got))


def field(addr, key):
    norm, _ = hk.normalize(addr)
    return hk.parse(norm)[key]


# --- 归一化 ---------------------------------------------------------------
check("全角逗号归一", hk.normalize("慶盛閣，穗禾苑，穗禾路，沙田，新界")[0],
      "慶盛閣,穗禾苑,穗禾路,沙田,新界")
check("全角逗号报格式问题", hk.normalize("A，B")[1], ["FULLWIDTH_PUNCT"])
check("尾部空白", hk.normalize("華園樓,竹園南邨,竹園道,竹園,九龍 ")[1],
      ["TRAILING_WHITESPACE"])
check("座号小写转大写", hk.normalize("藍田鯉安苑鯉景閣e座")[0], "藍田鯉安苑鯉景閣E座")
check("空段剔除", hk.normalize("A,,B")[0], "A,B")

# --- 连写地址切分 ---------------------------------------------------------
# 贪婪切分会切成 "美城" + "苑貴城閣", 这里锁死正确结果
check("連寫-屋苑", field("沙田大圍美城苑貴城閣", "estate"), "美城苑")
check("連寫-座", field("沙田大圍美城苑貴城閣", "block"), "貴城閣")
check("連寫-細區", field("沙田大圍美城苑貴城閣", "district"), "大圍")
check("連寫-上级區", field("沙田大圍美城苑貴城閣", "district_parent"), "沙田")

check("連寫-華庭屋苑", field("馬鞍山翠擁華庭第9座", "estate"), "翠擁華庭")
check("連寫-華庭座", field("馬鞍山翠擁華庭第9座", "block"), "第9座")
check("連寫-華庭區", field("馬鞍山翠擁華庭第9座", "district"), "馬鞍山")

check("連寫-第一城", field("沙田第一城第一座", "estate"), "第一城")
check("連寫-第一座", field("沙田第一城第一座", "block"), "第一座")

check("連寫-鯉安苑", field("藍田鯉安苑鯉景閣e座", "estate"), "鯉安苑")
check("連寫-鯉景閣", field("藍田鯉安苑鯉景閣E座", "block"), "鯉景閣E座")

check("連寫-日出康城期数", field("日出康城XIIB期 2B座", "phase"), "XIIB期")
check("連寫-日出康城座", field("日出康城XIIB期 2B座", "block"), "2B座")

# --- 分段地址 -------------------------------------------------------------
check("分段-大區", field("愉澤閣,愉田苑,銀城街,沙田,新界", "region"), "新界")
check("分段-屋苑", field("愉澤閣,愉田苑,銀城街,沙田,新界", "estate"), "愉田苑")
check("分段-座", field("愉澤閣,愉田苑,銀城街,沙田,新界", "block"), "愉澤閣")
check("分段-細區優先", field("第1座,名城1期,美田路,大圍,沙田,新界", "district"), "大圍")
check("分段-上级區", field("第1座,名城1期,美田路,大圍,沙田,新界", "district_parent"), "沙田")
check("分段-期数抽出", field("第1座,名城1期,美田路,大圍,沙田,新界", "phase"), "1期")
check("分段-屋苑去期", field("第1座,名城1期,美田路,大圍,沙田,新界", "estate"), "名城")
check("噪声剥离-大區", field("第七座,順利紀律部隊宿舍,利安道,牛頭角,九龍樓層", "region"), "九龍")
check("噪声剥离-噪声", field("第七座,順利紀律部隊宿舍,利安道,牛頭角,九龍樓層", "noise"), "樓層")
check("無未歸類片段", field("第七座,順利紀律部隊宿舍,利安道,牛頭角,九龍樓層", "leftover"), [])

# --- 数值转换 -------------------------------------------------------------
check("中文数字-三十", sc.cn_to_int("三十"), 30)
check("中文数字-十七", sc.cn_to_int("十七"), 17)
check("中文数字-七", sc.cn_to_int("七"), 7)
check("座号取值-第三十座", sc.block_number("第三十座"), 30)
check("座号取值-2B座", sc.block_number("2B座"), 2)
check("罗马数字-XII", sc.roman_to_int("XII"), 12)

# --- 规则命中 -------------------------------------------------------------
LINES = [ln.rstrip("\n") for ln in
         open(os.path.join(ROOT, "data", "addresses.txt"), encoding="utf-8")
         if ln.strip()]
records = [sc.Record(i, ln) for i, ln in enumerate(LINES, 1)]
for r in records:
    sc.check_row_rules(r)
sc.check_cross_rules(records)
by_idx = {r.idx: r for r in records}


def codes(i):
    return set(by_idx[i].codes)


check("总条数", len(records), 31)
check("錯别字命中", "A002" in codes(5), True)
check("噪声命中", "A001" in codes(27), True)
check("原文重复命中", "D001" in codes(1), True)
check("详略不一只标残缺条", ("D003" in codes(8), "D003" in codes(10)), (True, False))
check("同族补全命中", "A006" in codes(11), True)
check("同族补全建议", [h[1] for h in by_idx[11].hits if h[0] == "A006"][0],
      "同族参考第 9 条, 建议补全为「愉和閣,愉田苑,銀城街,沙田,新界」")
check("大座号命中", "A003" in codes(29), True)
check("十七座不误报", "A003" in codes(30), False)
check("罗马期数命中", "F007" in codes(25), True)
check("完整地址无命中", codes(31), set())
check("仅格式问题不算 bad case", by_idx[23].is_bad_case, False)
check("缺大區但有地區降为一般", by_idx[3].max_severity, "一般")
check("缺大區且缺地區为严重", by_idx[25].max_severity, "严重")
check("bad case 数", sum(1 for r in records if r.is_bad_case), 22)

# --- 输出列 ---------------------------------------------------------------
row = sc.row_of(by_idx[27])
check("输出列数", len(row), len(sc.HEADERS))
check("描述不含竖线(会破坏 markdown 表格)",
      "|" in row[sc.HEADERS.index("问题描述")], False)

if FAILED:
    print("失败 %d 项:\n" % len(FAILED) + "\n".join(FAILED))
    sys.exit(1)
print("全部通过")

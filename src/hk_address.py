# -*- coding: utf-8 -*-
"""香港地址归一化与结构化解析.

本模块只做两件事:
1. normalize(): 把原始地址串清洗成统一格式, 并记录清洗过程中发现的格式问题.
2. parse(): 把地址拆成 大区 / 地區 / 街道 / 屋苑 / 座-期 五个字段.

解析策略:
- 有分隔符(逗号)的地址: 逐段分类, 香港地址习惯由小到大书写.
- 无分隔符的连写地址: 从尾部向前"剥离"(peel) token, 置信度标记为低.
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# 参考词表
# --------------------------------------------------------------------------

# 大区
REGIONS = ["九龍", "九龙", "新界", "港島", "香港島", "香港岛", "香港"]

# 地區 / 常见地名(18區 + 口语地名). 长名在前, 保证最长匹配优先.
DISTRICTS = [
    "香港仔", "堅尼地城", "紅磡", "中西區", "灣仔", "東區", "南區",
    "油尖旺", "尖沙咀", "旺角", "油麻地", "深水埗", "長沙灣", "九龍城",
    "土瓜灣", "黃大仙", "鑽石山", "慈雲山", "竹園", "樂富", "觀塘",
    "牛頭角", "藍田", "油塘", "秀茂坪", "新蒲崗", "葵涌", "青衣",
    "葵青", "荃灣", "屯門", "元朗", "天水圍", "上水", "粉嶺", "北區",
    "大埔", "沙田", "大圍", "馬鞍山", "火炭", "西貢", "將軍澳", "馬灣",
    "東涌", "離島", "赤柱", "薄扶林", "北角", "鰂魚涌", "柴灣", "筲箕灣",
]
DISTRICTS.sort(key=len, reverse=True)

# 街道后缀
STREET_SUFFIX = ("道", "路", "街", "徑", "径", "巷", "里", "坊", "廣場", "大道")

# 屋苑 / 屋邨 后缀
ESTATE_SUFFIX = (
    "苑", "邨", "村", "花園", "花园", "華庭", "华庭", "庭", "園", "园",
    "城", "灣", "湾", "山莊", "山庄", "廣場", "广场", "中心", "宿舍",
    "大廈", "大厦", "居", "軒", "轩",
)

# 座 / 期 的匹配模式(从最具体到最宽松)
CN_NUM = "一二三四五六七八九十百零兩两"
BLOCK_PATTERNS = [
    rf"第[{CN_NUM}]+[座期]",              # 第七座 / 第三十座 / 第2期
    r"第\s*\d+\s*[座期]",                  # 第9座 / 第1座
    r"\d+[A-Za-z]?\s*座",                  # 2B座 / 9座
    r"[A-Za-z]\s*座",                      # B座 / e座
    r"[IVXLCDM]+[A-Za-z]?\s*期",           # XIIB期 (罗马数字期数)
    r"\d+[A-Za-z]?\s*期",                  # 1期
    rf"[{CN_NUM}]+\s*期",                  # 二期
]
BLOCK_RE = re.compile("(?:%s)" % "|".join(BLOCK_PATTERNS))
BLOCK_TAIL_RE = re.compile("(?:%s)$" % "|".join(BLOCK_PATTERNS))

# 楼宇名(閣/樓)——既可能是座名, 也可能是屋苑名, 靠上下文区分
BUILDING_TAIL_RE = re.compile(r"[一-鿿]{1,3}[閣樓阁楼]$")

# 末尾残留的表单字段名(数据采集时未填写却被拼进地址)
NOISE_TOKENS = ["樓層", "楼层", "座數", "座数", "室號", "室号", "樓宇", "楼宇",
                "單位", "单位", "門牌", "门牌", "地址", "請選擇", "请选择", "無", "无"]

# 常见形近错字: {疑似错字: 正确字}. 仅用于"疑似", 不自动改写.
SUSPECT_CHAR_MAP = {"間": "閣", "间": "阁", "閤": "閣"}

FULLWIDTH_PUNCT = "，、；；：（）　"


# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------

def normalize(raw):
    """清洗地址, 返回 (归一化地址, 格式问题列表).

    只做无损清洗: 统一分隔符/大小写/空白, 不改写任何地名用字.
    """
    issues = []
    s = raw

    if s != s.strip():
        issues.append("TRAILING_WHITESPACE")
    if any(ch in s for ch in FULLWIDTH_PUNCT):
        issues.append("FULLWIDTH_PUNCT")
    if re.search(r"[Ａ-Ｚａ-ｚ０-９]", s):
        issues.append("FULLWIDTH_ALNUM")

    # 全角 -> 半角(NFKC 会顺带处理全角逗号/空格/字母数字)
    s = unicodedata.normalize("NFKC", s)
    # 统一分隔符
    s = re.sub(r"[、;；]", ",", s)
    s = s.strip()

    # 分段并去掉空段
    parts = [p.strip() for p in s.split(",")]
    if any(p == "" for p in parts):
        issues.append("EMPTY_SEGMENT")
    parts = [p for p in parts if p]

    # 座号中的英文字母统一大写: e座 -> E座
    fixed = []
    for p in parts:
        np = re.sub(r"([A-Za-z])(\s*)([座期])", lambda m: m.group(1).upper() + m.group(3), p)
        np = re.sub(r"\s+", " ", np).strip()
        if np != p:
            if re.search(r"[a-z]\s*[座期]", p):
                issues.append("LOWERCASE_BLOCK_LETTER")
        fixed.append(np)

    return ",".join(fixed), sorted(set(issues))


# --------------------------------------------------------------------------
# 单段分类
# --------------------------------------------------------------------------

def is_region(seg):
    return any(seg == r or seg.endswith(r) for r in REGIONS)


def match_district(seg):
    """返回该段命中的地名(可能是完整段, 也可能是前缀)."""
    for d in DISTRICTS:
        if seg == d:
            return d
    return None


def is_street(seg):
    return seg.endswith(STREET_SUFFIX)


def is_estate(seg):
    if seg.endswith(ESTATE_SUFFIX):
        return True
    # "名城1期" / "觀龍樓第2期" 这类带期数的屋苑名
    if BLOCK_TAIL_RE.search(seg) and len(BLOCK_TAIL_RE.sub("", seg)) >= 2:
        return True
    return False


def is_block(seg):
    if BLOCK_TAIL_RE.fullmatch(seg):
        return True
    if BLOCK_RE.fullmatch(seg):
        return True
    if BUILDING_TAIL_RE.fullmatch(seg):
        return True
    return False


# --------------------------------------------------------------------------
# 结构化解析
# --------------------------------------------------------------------------

class Parsed(dict):
    """解析结果: region/district/street/estate/block/phase + confidence.

    district        最细一级地名(如 大圍)
    district_parent 上一级地名(如 沙田), 香港地址常见"細區+大區"并列写法
    building        楼宇名(X閣/X樓), 用于同一楼宇的详略写法比对
    noise           从字段里剥出来的噪声内容(如 "九龍樓層" 里的 "樓層")
    """

    FIELDS = ("region", "district", "district_parent", "street", "estate",
              "block", "phase", "building", "noise")

    def __init__(self, **kw):
        super().__init__({f: "" for f in self.FIELDS})
        self["confidence"] = "high"
        self["leftover"] = []
        self.update(kw)


def parse(norm):
    """把归一化地址解析成结构化字段."""
    parts = [p for p in norm.split(",") if p]
    if len(parts) >= 2:
        return _parse_segmented(parts)
    return _parse_concatenated(parts[0] if parts else "")


def split_region_noise(seg):
    """把 "九龍樓層" 这类"大區+残留字段"的段拆开, 返回 (大區, 噪声)."""
    for r in REGIONS:
        if seg.startswith(r) and seg != r:
            tail = seg[len(r):]
            if tail in NOISE_TOKENS:
                return r, tail
    return None, None


def _parse_segmented(parts):
    """逗号分隔的地址: 香港习惯由小到大, 但不强制依赖顺序."""
    p = Parsed()
    for seg in parts:
        if not p["region"]:
            region, noise = split_region_noise(seg)
            if region:
                p["region"], p["noise"] = region, noise
                continue
        if not p["region"] and is_region(seg):
            p["region"] = seg
            continue
        d = match_district(seg)
        if d and not p["district"]:
            p["district"] = seg
            continue
        if d and p["district"] and not p["district_parent"]:
            # 香港常见"細區+大區"并列(大圍 + 沙田): 先出现的更细, 后出现的作上级
            p["district_parent"] = seg
            continue
        if d:
            p["leftover"].append(seg)
            continue
        if not p["street"] and is_street(seg):
            p["street"] = seg
            continue
        if not p["block"] and is_block(seg):
            p["block"] = seg
            continue
        if not p["estate"] and is_estate(seg):
            p["estate"] = seg
            continue
        p["leftover"].append(seg)

    # 屋苑名里内嵌的期数抽出来: 名城1期 -> estate=名城, phase=1期
    if p["estate"]:
        m = BLOCK_TAIL_RE.search(p["estate"])
        if m and "期" in m.group(0):
            p["phase"] = m.group(0)
            p["estate"] = p["estate"][: m.start()]

    # leftover 里若还有像屋苑的段, 补进 estate
    rest = []
    for seg in p["leftover"]:
        if not p["estate"] and (is_estate(seg) or len(seg) >= 3):
            p["estate"] = seg
        else:
            rest.append(seg)
    p["leftover"] = rest
    return p


def _parse_concatenated(s):
    """无分隔符的连写地址: 从尾部剥离, 置信度标低."""
    p = Parsed(confidence="low")
    if not s:
        return p
    rest = s.replace(" ", "")
    blocks = []

    # 1. 反复剥离尾部的 座/期
    while True:
        m = BLOCK_TAIL_RE.search(rest)
        if not m or m.start() == 0:
            break
        blocks.append(m.group(0))
        rest = rest[: m.start()]

    # 2. 剥离尾部的 X閣 / X樓 (座名)
    #    楼宇名通常是"两字+閣", 但也有一字/三字; 逐个长度试, 取"剩余部分仍像地址"的那个,
    #    否则 "美城苑貴城閣" 会被贪婪切成 "美城" + "苑貴城閣".
    for n in (2, 3, 1):
        m = re.search(r"[一-鿿]{%d}[閣樓阁楼]$" % n, rest)
        if not m or m.start() == 0:
            continue
        head = rest[: m.start()]
        if head.endswith(ESTATE_SUFFIX) or peel_districts(head)[1] == "":
            p["building"] = m.group(0)
            blocks.append(m.group(0))
            rest = head
            break

    # 3. 剥离屋苑(以屋苑后缀结尾)
    for sfx in sorted(ESTATE_SUFFIX, key=len, reverse=True):
        if rest.endswith(sfx) and len(rest) > len(sfx):
            districts, remainder = peel_districts(rest)
            if remainder:
                p["estate"] = remainder
                rest = "".join(districts)
            break

    # 4. 剩下的开头部分当作地名; 连写地址由大到小, 最后一个最细
    districts, remainder = peel_districts(rest)
    if districts:
        p["district"] = districts[-1]
        if len(districts) > 1:
            p["district_parent"] = districts[-2]
            p["leftover"].extend(districts[:-2])
    if remainder:
        if not p["estate"]:
            p["estate"] = remainder
        else:
            p["leftover"].append(remainder)

    blocks.reverse()
    phases = [b for b in blocks if "期" in b]
    seats = [b for b in blocks if "期" not in b]
    p["phase"] = "".join(phases)
    p["block"] = "".join(seats)
    return p


def match_prefix_district(s):
    """返回 s 开头命中的地名, 没有则返回空串."""
    for d in DISTRICTS:
        if s.startswith(d):
            return d
    return ""


def peel_districts(s):
    """连续剥离开头的地名, 返回 (地名列表, 剩余部分).

    "沙田大圍美城苑" -> (["沙田", "大圍"], "美城苑")
    """
    districts = []
    while s:
        d = match_prefix_district(s)
        if not d:
            break
        districts.append(d)
        s = s[len(d):]
    return districts, s


def canonical(parsed):
    """输出规范书写(由小到大, 半角逗号分隔)."""
    order = ["block", "phase", "estate", "street", "district",
             "district_parent", "region"]
    vals = [parsed[k] for k in order if parsed.get(k)]
    return ",".join(vals)


def dedup_key(parsed, norm):
    """去重键: 忽略书写顺序/格式差异, 只看核心字段."""
    core = [parsed.get("block", ""), parsed.get("phase", ""),
            parsed.get("estate", ""), parsed.get("district", "")]
    key = "|".join(core).strip("|")
    return key if key.replace("|", "") else norm


def building_name(parsed):
    """取楼宇名(X閣/X樓), 用于"同一楼宇、详略两种写法"的比对."""
    for field in ("building", "block", "estate"):
        v = parsed.get(field, "")
        if v and BUILDING_TAIL_RE.fullmatch(v):
            return v
    return ""

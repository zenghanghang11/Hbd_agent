# -*- coding: utf-8 -*-
"""接口提取链路的回归测试: python3 tests/test_recog_y.py"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, os.path.join(ROOT, "src"))

import cmhk_api
import curl_parser
import fetch_recog_y as fry

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append("%s\n    期望: %r\n    实际: %r" % (name, want, got))


# --- curl 解析 -------------------------------------------------------------
reqs = curl_parser.parse_curl_file(os.path.join(FIX, "sample.curl"))
check("解析出两条请求", len(reqs), 2)

search = curl_parser.find_by_path(reqs, "searchAddress")
detail = curl_parser.find_by_path(reqs, "getAddressDetail")
check("找到 searchAddress", search is not None, True)
check("找到 getAddressDetail", detail is not None, True)
check("方法默认 POST", search.method, "POST")
check("URL 保留反爬令牌", "XGiOG2f705=FAKE_TOKEN_FOR_TEST_ONLY" in search.url, True)
check("-b 解析 Cookie", search.cookies.get("zA7uZWGUB1"), "FAKE")
check("-H cookie 也解析", detail.cookies.get("lang"), "zh-HK")
check("cookie 不混进 headers", "cookie" in detail.headers, False)
check("请求头保留", search.headers.get("api-version"), "2")
check("body 解码", json.loads(search.body_params()["busInfo"])["keyword"], "愉澤閣")
check("-X 显式方法", detail.method, "POST")

# 单条 curl 也能解析
one = curl_parser.parse_curl("curl 'https://x.test/a?b=1' -H 'k: v' --data-raw 'p=1'")
check("单条 curl", (one.path, one.headers["k"], one.body), ("/a", "v", "p=1"))

# --- 响应提取 --------------------------------------------------------------
search_fx = json.load(open(os.path.join(FIX, "searchAddress.sample.json"), encoding="utf-8"))
detail_fx = json.load(open(os.path.join(FIX, "getAddressDetail.sample.json"), encoding="utf-8"))

cands = cmhk_api.extract_candidates(search_fx["馬鞍山翠擁華庭第9座"])
check("候选数", len(cands), 2)
check("候选顺序(Y 在前)", [c["recogFlag"] for c in cands], ["Y", "N"])
check("buildingCode", cmhk_api.building_code(cands[0]), "7574190008")
check("description 去标签", cmhk_api.plain_description(cands[0]),
      "第9座,翠擁華庭,沙安街,馬鞍山,新界")
check("空候选", cmhk_api.extract_candidates(search_fx["愉和閣"]), [])

# CMHK 的 addressCode 优先于其他运营商
cand = {"carrierInfo": [{"addressCode": "999", "carrier": "HKBN"},
                        {"addressCode": "111", "carrier": "CMHK"}]}
check("优先取 CMHK 的 addressCode", cmhk_api.building_code(cand), "111")

# 结构 A: 楼层内嵌房间
floors, flats, pairs = cmhk_api.extract_floors_flats(
    detail_fx["第9座,翠擁華庭,沙安街,馬鞍山,新界"])
check("楼层(保序)", floors, ["10", "11", "12"])
check("房间(保序去重)", flats, ["A", "B", "E"])
check("楼层-房间对", pairs,
      [("10", "A"), ("10", "B"), ("10", "E"), ("11", "A"), ("11", "E"), ("12", "A")])

# 结构 B: 楼层与房间平铺
floors2, flats2, pairs2 = cmhk_api.extract_floors_flats(detail_fx["康寧閣,康寧道,觀塘,九龍"])
check("平铺-楼层", floors2, ["1", "2", "3"])
check("平铺-房间", flats2, ["A", "B"])
check("平铺-无法给出配对", pairs2, [])

# --- 展开逻辑 --------------------------------------------------------------
check("有配对时直接用", fry.expand_pairs(
    {"pairs": [("1", "A")], "floors": ["1"], "flats": ["A"]}),
    [("1", "A", "接口(按层给出房间)")])
check("无配对时笛卡尔积并标注", fry.expand_pairs(
    {"pairs": [], "floors": ["1", "2"], "flats": ["A"]}),
    [("1", "A", "笛卡尔积(推断)"), ("2", "A", "笛卡尔积(推断)")])
check("只有楼层", fry.expand_pairs({"pairs": [], "floors": ["1"], "flats": []}),
      [("1", "", "仅有楼层")])
check("都没有", fry.expand_pairs({"pairs": [], "floors": [], "flats": []}), [])

# --- 查询词形态 ------------------------------------------------------------
check("raw 保持原样", fry.build_keyword("慶盛閣，穗禾苑，穗禾路，沙田，新界", "raw"),
      "慶盛閣，穗禾苑，穗禾路，沙田，新界")
check("normalized 转半角", fry.build_keyword("慶盛閣，穗禾苑，穗禾路，沙田，新界", "normalized"),
      "慶盛閣,穗禾苑,穗禾路,沙田,新界")
check("canonical 重排为 座,屋苑,街道,地區,大區",
      fry.build_keyword("馬鞍山翠擁華庭第9座", "canonical"), "第9座,翠擁華庭,馬鞍山")

# --- 端到端(mock) ----------------------------------------------------------
client = cmhk_api.MockClient(FIX)
records = []
for i, raw in enumerate(["馬鞍山翠擁華庭第9座", "康寧間,康寧道,觀塘,九龍", "愉和閣"], 1):
    r = fry.AddressResult(i, raw, fry.build_keyword(raw, "raw"))
    r.offline = "测试"
    records.append(r)
aborted = fry.process(records, client)

check("未中止", aborted, "")
check("命中数", [r.hit for r in records], [True, False, False])
check("Y 地址的楼层", records[0].y_items[0]["floors"], ["10", "11", "12"])
check("Y 地址的房间", records[0].y_items[0]["flats"], ["A", "B", "E"])
check("明细状态", records[0].y_items[0]["detail_status"], "成功")
check("N 候选不进结果", records[1].y_items, [])
check("未命中原因-有候选无Y", records[1].reason, "有 1 条候选但无一条 recogFlag=Y(N×1)")
check("未命中原因-查无此址", records[2].reason, "接口无任何候选地址(查无此址)")
check("展开行数", len(fry.expand_rows(records)), 6)
check("主表列数", len(fry.main_rows(records)[0]), len(fry.MAIN_HEADERS))
check("未命中表列数", len(fry.miss_rows(records)[0]), len(fry.MISS_HEADERS))


# --- 令牌失效必须中止而不是静默产出空结果 ----------------------------------
class ExpiredClient:
    stats = {"requests": 0, "errors": 0}

    def search_address(self, keyword, tag=""):
        raise cmhk_api.TokenExpired("HTTP 403")

    def get_address_detail(self, candidate, tag=""):
        raise cmhk_api.TokenExpired("HTTP 403")


rs = [fry.AddressResult(1, "愉澤閣", "愉澤閣")]
check("令牌失效会中止", fry.process(rs, ExpiredClient()), "HTTP 403")

if FAILED:
    print("失败 %d 项:\n" % len(FAILED) + "\n".join(FAILED))
    sys.exit(1)
print("全部通过")

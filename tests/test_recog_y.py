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

# 未知布尔选项不能把 URL 当成它的取值吃掉
weird = curl_parser.parse_curl("curl --location-trusted 'https://x.test/a' -H 'k: v'")
check("未知选项后仍能取到 URL", weird.path, "/a")

# 两条命令之间夹中文说明时, 必须仍然切成两条 —— 否则后一条的
# 请求头/Cookie 会覆盖前一条, 拼出张冠李戴的请求
mixed = ("curl 'https://x.test/searchAddress' -H 'a: 1' -b 'c=1' "
         "--data-raw 'p=1' ; 将返回值保存后调接口curl 'https://x.test/getInstallInfo' "
         "-H 'a: 2' -b 'c=2' --data-raw 'p=2'")
cmds = curl_parser.split_curl_commands(mixed)
check("夹说明文字也能切成两条", len(cmds), 2)
p1, p2 = (curl_parser.parse_curl(c) for c in cmds)
check("第一条不被第二条污染", (p1.path, p1.headers["a"], p1.cookies["c"]),
      ("/searchAddress", "1", "1"))
check("第二条独立", (p2.path, p2.headers["a"], p2.cookies["c"]),
      ("/getInstallInfo", "2", "2"))

# 说明文字被写进 --data-raw 引号内时, 只取前缀的合法 JSON
check("容忍 body 尾部粘连说明", cmhk_api.json_prefix(
    '{"buildingCode":"1","floor":"10"}的"recogFlag"返回值为"Y"给我记录下来'),
    {"buildingCode": "1", "floor": "10"})
check("非法 JSON 返回空", cmhk_api.json_prefix("不是json"), {})

# --- 响应提取 --------------------------------------------------------------
search_fx = json.load(open(os.path.join(FIX, "searchAddress.sample.json"), encoding="utf-8"))
detail_fx = json.load(open(os.path.join(FIX, "getAddressDetail.sample.json"), encoding="utf-8"))

# 需求: 取 busiResp.busiDataResp 的第一个元素, 不做筛选
obj = cmhk_api.first_data_resp(search_fx["馬鞍山翠擁華庭第9座"])
check("取第一个元素", obj["value"], "第9座,翠擁華庭,沙安街,馬鞍山,新界")
check("buildingCode", cmhk_api.building_code(obj), "7574190008")
check("description 去标签", cmhk_api.plain_description(obj),
      "第9座,翠擁華庭,沙安街,馬鞍山,新界")
check("空列表返回 None", cmhk_api.first_data_resp(search_fx["愉和閣"]), None)
check("非 dict 不炸", cmhk_api.first_data_resp(None), None)

# CMHK 的 addressCode 优先于其他运营商
check("优先取 CMHK 的 addressCode", cmhk_api.building_code(
    {"carrierInfo": [{"addressCode": "999", "carrier": "HKBN"},
                     {"addressCode": "111", "carrier": "CMHK"}]}), "111")

# recogFlag 在 getInstallInfo 的响应里, 任意层级都要能取到
check("recogFlag 取值", cmhk_api.recog_flag({"busiResp": {"recogFlag": "Y"}}), "Y")
check("recogFlag 小写归一", cmhk_api.recog_flag({"a": {"b": {"recogFlag": "y"}}}), "Y")
check("recogFlag 缺失", cmhk_api.recog_flag({"busiResp": {}}), "")

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
check("平铺-无配对", pairs2, [])

# --- 查询词形态 ------------------------------------------------------------
check("raw 保持原样", fry.build_keyword("慶盛閣，穗禾苑，穗禾路，沙田，新界", "raw"),
      "慶盛閣，穗禾苑，穗禾路，沙田，新界")
check("normalized 转半角", fry.build_keyword("慶盛閣，穗禾苑，穗禾路，沙田，新界", "normalized"),
      "慶盛閣,穗禾苑,穗禾路,沙田,新界")
check("canonical 重排", fry.build_keyword("馬鞍山翠擁華庭第9座", "canonical"),
      "第9座,翠擁華庭,馬鞍山")

# --- 端到端(mock): 完整链路 -----------------------------------------------
client = cmhk_api.MockClient(FIX)
records = []
for i, raw in enumerate(["馬鞍山翠擁華庭第9座", "康寧間,康寧道,觀塘,九龍", "愉和閣"], 1):
    r = fry.AddressResult(i, raw, fry.build_keyword(raw, "raw"))
    records.append(r)
check("未中止", fry.process(records, client), "")

a, b, c = records
check("命中情况", [r.hit for r in records], [True, False, False])
# 10/A 是 N, 10/B 才是 Y —— 必须继续试而不是停在第一个组合
check("命中的 Floor/Flat", (a.hit_floor, a.hit_flat), ("10", "B"))
check("命中后立即停止(只探测 2 次)", a.probes, 2)
check("命中不再试剩余组合", a.probes < a.combo_total, True)
check("整栋试完仍未命中", (b.combo_total, b.probes), (6, 6))
check("未命中原因", b.reason, "6 个组合全部试完, 无 recogFlag=Y")
check("recogFlag 分布", b.flag_dist, "N×6")
check("查无此址", c.reason, "searchAddress 无结果")
check("查无此址不探测", c.probes, 0)

# 探测上限
records2 = [fry.AddressResult(1, "康寧間,康寧道,觀塘,九龍", "康寧間,康寧道,觀塘,九龍")]
fry.process(records2, cmhk_api.MockClient(FIX), max_probes=2)
check("探测上限生效", records2[0].probes, 2)
check("上限原因", records2[0].reason, "已达单地址探测上限 2, 未命中")

# --- 输出行 ---------------------------------------------------------------
check("结果表只含命中行", fry.main_rows(records), [[1, "馬鞍山翠擁華庭第9座", "10", "B"]])
check("结果表列数", len(fry.MAIN_HEADERS), 4)
check("明细表覆盖全部地址", len(fry.detail_rows(records)), 3)
check("明细列数", len(fry.detail_rows(records)[0]), len(fry.DETAIL_HEADERS))
check("未命中表列数", len(fry.miss_rows(records)[0]), len(fry.MISS_HEADERS))


# --- 令牌失效必须中止而不是静默产出空结果 ----------------------------------
class ExpiredClient:
    stats = {"requests": 0, "errors": 0}

    def search_address(self, keyword, tag=""):
        raise cmhk_api.TokenExpired("HTTP 403")


rs = [fry.AddressResult(1, "愉澤閣", "愉澤閣")]
check("令牌失效会中止", fry.process(rs, ExpiredClient()), "HTTP 403")
check("中止后不算已查询", rs[0].queried, False)
check("中止后原因不误导", rs[0].reason, "未查询(接口未调用或运行中止)")

if FAILED:
    print("失败 %d 项:\n" % len(FAILED) + "\n".join(FAILED))
    sys.exit(1)
print("全部通过")

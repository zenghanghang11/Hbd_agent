# 地址 bad case 筛查

两步:

1. **离线筛查** — 不依赖接口, 按规则判定地址本身的质量问题(缺字段/错别字/重复/格式).
2. **接口提取** — 按需求走完 searchAddress → getAddressDetail → getInstallInfo 三步,
   找出 `recogFlag=Y` 的 **Floor / Flat**; 整栋楼都找不到 Y 的地址就是 bad case.

```bash
pip install requests openpyxl

# 1) 离线筛查
python3 src/screen_bad_cases.py --input data/addresses.txt --outdir output

# 2) 接口提取(真机, 需先准备 curl 模板, 见下)
python3 src/fetch_recog_y.py --mode live --curl-file config/cmhk.curl

# 回归测试
python3 tests/test_screening.py && python3 tests/test_recog_y.py
```

---

## 一、接口提取(`src/fetch_recog_y.py`)

### 流程(按需求说明)

```
我输入的地址
   └─ searchAddress(busInfo={"keyword": 地址})
        └─ 取 busiResp.busiDataResp 列表的【第一个元素】= busiDataRespObj
             └─ getAddressDetail(orderStr=busiDataRespObj)
                  └─ 返回值 busiResp = busiRespObj, 取该 BuildingCode 的所有 Floor 与所有 Flat
                       └─ 遍历 Floor × Flat, 逐个调 getInstallInfo
                            └─ 响应 recogFlag == "Y" → 记录(原始输入 + Floor + Flat), 跳到下一个地址
```

要点:

- 搜索结果**取第一个元素, 不做筛选** —— `recogFlag` 不在 `searchAddress` 上, 而在
  **`getInstallInfo` 的响应**里.
- 命中一个组合就**停止**该地址的探测("找到的话当前楼就可以 continue 了").
- 一个 Floor×Flat 组合都没命中的地址, 就是要人工复核的 **bad case**.

### 准备 curl 模板

三个接口都带反爬令牌(URL 参数 `XGiOG2f705` + Cookie `zA7uZWGUB1`), **每次请求都变且会过期**,
所以不在代码里硬编码, 而是从浏览器现抓:

1. 打开 https://www.hk.chinamobile.com/tc/home-family/broadband;
2. 查一个地址 → 选中一条结果 → 再选楼层和房间号(把三个接口都触发一遍);
3. F12 网络面板, 对 `searchAddress`、`getAddressDetail`、`getInstallInfo` 分别
   **Copy as cURL (bash)**;
4. 三条都粘进 `config/cmhk.curl`(格式见 `config/cmhk.curl.example`);
5. 跑 live 模式.

令牌失效时脚本会**明确报错并中止**, 提示重抓 curl —— 不会静默产出一堆空结果.
`config/cmhk.curl` 含登录 Cookie, 已在 `.gitignore` 里排除, 不要提交.

脚本按 "curl + URL" 出现的位置切分命令, 所以**命令之间夹中文说明也不影响**;
说明文字被写进 `--data-raw` 引号里时, 只取前缀的合法 JSON.

### 三种模式

| 模式 | 说明 |
|---|---|
| `--mode live` | 真机调接口, 产出真实结果 |
| `--mode mock` | 用 `tests/fixtures` 的构造样例跑通全流程, 验证提取逻辑, 不发网络请求 |
| `--mode plan` | 只产出查询计划(输入地址 → 查询关键词 + 离线筛查结论), 不发网络请求 |

常用参数:

- `--max-probes N` — **单个地址最多试多少个 Floor×Flat 组合, 默认 60**.
  命中通常 1 次就够; 但 bad case 会把整栋楼试遍, 一栋 30 层×8 房 = 240 次请求.
  这个上限是防止一条烂地址把整批跑挂, 调 0 表示不限.
- `--keyword-mode raw|normalized|canonical` — 送给接口的查询词形态. **默认 `raw`**:
  bad case 筛查要看系统对**原始脏数据**的反应. 想验证"清洗后能不能查到",
  换 `normalized` 或 `canonical` 再跑一遍对比.
- `--limit N` — 只跑前 N 条, 先试跑确认令牌有效.
- `--delay 1.0` — 请求间隔秒数.

### 产出(`output/recog_y/`)

| 文件 | 内容 |
|---|---|
| `recog_y_result.xlsx` | 四个表, 主交付件 |
| `recog_y_result.csv` | **结果表**: 序号 / 输入地址 / Floor / Flat —— 需求要的就是这个 |
| `recog_y_result_detail.csv` | 明细: 每个地址走到哪一步、试了多少组合、recogFlag 分布 |
| `recog_y_result_miss.csv` | 未命中(bad case)清单 |
| `recog_y_result.json` | 完整结构化结果 |
| `recog_y_report.md` | 人读版报告 |
| `raw/` | live 模式下每次调用的原始响应, 便于核对接口结构(已 gitignore) |

未命中会区分几种原因, 便于定位问题出在哪一环:

- `searchAddress 无结果` —— 查无此址;
- `getAddressDetail 未返回楼层/房间` —— 地址查得到但楼里没数据;
- `N 个组合全部试完, 无 recogFlag=Y` —— 整栋楼都不可装;
- `已达单地址探测上限` —— 没试完就停了, 需要调大 `--max-probes` 再确认.

### 响应结构说明

抓包文件里只有**请求**没有响应. 响应的关键路径来自需求说明本身
(`busiResp.busiDataResp`、`busiRespObj`、`Floor`/`Flat`、`recogFlag`), 其余字段名按
请求体反推. `src/cmhk_api.py` 因此采用**在任意层级找字段**的容错解析, 不假设固定嵌套路径,
同时兼容"楼层内嵌房间"与"楼层房间平铺"两种结构.

真机第一次跑完后, 用 `output/recog_y/raw/` 里的真实响应替换
`tests/fixtures/*.sample.json`, 回归测试就锁住真实结构了.

---

## 二、离线筛查(`src/screen_bad_cases.py`)

命中任一「严重」或「一般」规则即计为 bad case; 只命中「轻微」规则的可由
`hk_address.normalize()` 自动修复, 不计入.

| 档 | 含义 | 处理方式 |
|---|---|---|
| 严重 P0 | 无法可靠定位到唯一楼宇 | 必须人工修正 |
| 一般 P1 | 影响去重与字段一致性 | 建议修正 |
| 轻微 P2 | 纯格式问题 | 脚本自动修复 |

| 代码 | 类别 | 严重度 | 说明 |
|---|---|---|---|
| C001 | 完整性 | 严重 / 一般* | 缺少大區(九龍/新界/港島). *地區已知时可反查, 降为一般 |
| C002 | 完整性 | 严重 | 缺少地區 |
| C003 | 完整性 | 严重 | 只有座/閣名, 缺屋苑, 无法唯一定位 |
| C004 | 完整性 | 一般 | 缺少街道 |
| C005 | 完整性 | 一般 | 缺少座/期号 |
| A001 | 异常 | 严重 | 尾部粘连表单字段名等噪声(如 `九龍樓層`) |
| A002 | 异常 | 严重 | 疑似形近错别字(如 `康寧間` → `康寧閣`) |
| A003 | 异常 | 一般 | 座号数值偏大, 疑似超出屋苑实际座数 |
| A004 | 异常 | 一般 | 存在无法归类的地址片段 |
| A005 | 异常 | 一般 | 连写地址, 结构解析置信度低 |
| A006 | 异常 | 一般 | 可依据同批数据中的同族记录补全 |
| D001–D003 | 重复 | 一般 | 原文重复 / 归一化后重复 / 同一楼宇详略两种写法 |
| F001–F007 | 格式 | 轻微 | 全角标点 / 首尾空白 / 座号小写 / 全角字母数字 / 空段 / 无分隔符连写 / 罗马数字期数 |

产出见 `output/`: `bad_case_screening.xlsx` / `.csv` / `.json` 与 `bad_case_report.md`.

---

## 三、代码结构

```
src/hk_address.py        归一化 + 结构化解析(大區/地區/街道/屋苑/期/座), 无判定逻辑
src/screen_bad_cases.py  离线规则引擎 + 报告生成; 规则集中在 RULES 字典
src/curl_parser.py       解析 "Copy as cURL" 命令 -> URL/请求头/Cookie/body
src/cmhk_api.py          接口客户端 + 容错响应解析(限速/重试/令牌失效识别/原始响应留档)
src/fetch_recog_y.py     串起来: 搜地址 -> 取第一个结果 -> 取楼层房间 -> 遍历探测 -> 出表
tests/                   回归测试与构造样例
```

### 解析要点(`hk_address.py`)

- **分段地址**(逗号分隔): 逐段分类, 不依赖固定顺序; 香港常见"細區+大區"并列写法
  (`大圍,沙田`)拆到 `地區` 与 `地區(上级)` 两列.
- **连写地址**(无分隔符): 从尾部逐层剥离 座 → 期 → 楼宇名 → 屋苑 → 地名.
  楼宇名按 2/3/1 字逐个长度试探, 取"剩余部分仍像地址"的切法 —— 否则
  `美城苑貴城閣` 会被贪婪切成 `美城` + `苑貴城閣`. 这类地址统一标记 A005(置信度低).

## 四、调整方法

- 改判定口径: 编辑 `screen_bad_cases.py` 的 `RULES`, 无需动解析层.
- 新增地名/屋苑后缀/噪声词: 编辑 `hk_address.py` 顶部的 `DISTRICTS` / `ESTATE_SUFFIX` /
  `NOISE_TOKENS` / `SUSPECT_CHAR_MAP`.
- 座号阈值: `screen_bad_cases.py` 的 `HIGH_BLOCK_THRESHOLD`(默认 20).
- 接口响应字段对不上: 改 `cmhk_api.py` 的 `FLOOR_KEYS` / `FLAT_KEYS`, 或
  `first_data_resp` / `recog_flag` 的兜底条件.

## 五、已知边界

- **A002/A003/A006 是启发式提示, 不是结论**. 离线部分没有接入香港屋苑权威数据, 所以
  "疑似错别字""座号偏大""同族补全"都需人工或接口核实, 脚本不会自动改写任何地名用字.
- **接口响应结构是从请求体反推的**, 首次真机跑通后请核对 `output/recog_y/raw/` 并更新 fixture.
- **`getAddressDetail` 只给平铺的楼层表和房间表时**(没说明哪层有哪些房间), 脚本按
  笛卡尔积遍历, 其中部分组合可能实际不存在 —— 这只影响探测次数, 不影响命中结果.
- **探测上限默认 60**: 未命中且 `实际探测次数 == 60` 的地址, 是"没试完"而不是"确认不可装",
  需要调大 `--max-probes` 复跑确认.
- 反爬令牌会过期, 长列表建议分批跑(`--limit`), 或在令牌失效后重抓 curl 续跑.

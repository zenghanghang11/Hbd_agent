# 地址 bad case 筛查

两步:

1. **离线筛查** — 不依赖接口, 按规则判定地址本身的质量问题(缺字段/错别字/重复/格式).
2. **接口提取** — 调 CMHK 寬頻地址接口, 提取 `recogFlag=Y` 的地址及其**楼层**与**房间号**;
   查不到 Y 的那些就是要人工复核的 bad case.

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

### 接口链路

抓包里三个接口, 本脚本用前两个:

| 接口 | 请求体 | 作用 |
|---|---|---|
| `searchAddress` | `busInfo={"keyword":"<地址>"}` | 返回候选地址列表, 每条带 `recogFlag` |
| `getAddressDetail` | `orderStr=<搜索结果里的那条地址对象>` | 返回该楼宇的**楼层**与**房间号** |
| `getInstallInfo` | `busInfo={...,"floor":"10","flat":"E"}` | 安装信息, 本脚本不调用 |

`getAddressDetail` 的 `orderStr` 就是把 `searchAddress` 返回的地址对象原样回传
(`carrierInfo` / `clientType` / `ofcaCode` / `description` / `custCode` / `value`),
脚本按同样方式组装, 与页面行为一致.

### 准备 curl 模板

接口带反爬令牌(URL 参数 `XGiOG2f705` + Cookie `zA7uZWGUB1`), **每次请求都变且会过期**,
所以不在代码里硬编码, 而是从浏览器现抓:

1. 打开 https://www.hk.chinamobile.com/tc/home-family/broadband, 查一个地址并选中一条结果;
2. F12 网络面板, 对 `searchAddress` 和 `getAddressDetail` 分别 **Copy as cURL (bash)**;
3. 两条都粘进 `config/cmhk.curl`(照抄 `config/cmhk.curl.example` 的格式);
4. 跑 live 模式.

令牌失效时脚本会**明确报错并中止**, 提示重抓 curl —— 不会静默产出一堆空结果.
`config/cmhk.curl` 含登录 Cookie, 已在 `.gitignore` 里排除, 不要提交.

### 三种模式

| 模式 | 说明 |
|---|---|
| `--mode live` | 真机调接口, 产出真实结果 |
| `--mode mock` | 用 `tests/fixtures` 的构造样例跑通全流程, 验证提取逻辑, 不发网络请求 |
| `--mode plan` | 只产出查询计划(输入地址 → 查询关键词 + 离线筛查结论), 不发网络请求 |

常用参数:

- `--keyword-mode raw|normalized|canonical` — 送给接口的查询词形态. **默认 `raw`**:
  bad case 筛查要看系统对**原始脏数据**的反应, 所以原样送. 想验证"清洗后能不能查到",
  换 `normalized`(转半角/去空白)或 `canonical`(重排成 `座,屋苑,街道,地區,大區`) 再跑一遍对比.
- `--limit N` — 只跑前 N 条, 先试跑确认令牌有效.
- `--delay 1.0` — 请求间隔秒数.
- `--no-detail` — 只查地址不查楼层房间.

### 产出(`output/recog_y/`)

| 文件 | 内容 |
|---|---|
| `recog_y_result.xlsx` | 四个表, 主交付件 |
| `recog_y_result.csv` | `recogY地址` 表 |
| `recog_y_result_expand.csv` | `楼层房间展开` 表 |
| `recog_y_result_miss.csv` | `未命中(bad case)` 表 |
| `recog_y_result.json` | 完整结构化结果 |
| `recog_y_report.md` | 人读版报告 |
| `raw/` | live 模式下每次调用的原始响应, 便于核对接口结构(已 gitignore) |

四个表:

- **recogY地址** — 每条 `recogFlag=Y` 的地址一行: 匹配地址 / `buildingCode` / `ofcaCode` /
  运营商 / 楼层数 / 房间数 / 楼层列表 / 房间号列表 / 离线筛查结论.
- **楼层房间展开** — 拆成 `楼层×房间` 一行一个组合, 可直接当 `getInstallInfo` 的测试用例输入.
  `组合来源` 标 `接口(按层给出房间)` 的是接口明确给出的; 标 `笛卡尔积(推断)` 的是接口把楼层和
  房间分成两个平铺列表返回, 脚本无法确定哪层有哪些房间, **这类组合需要实际调用验证**.
- **未命中(bad case)** — 没有任何 `recogFlag=Y` 候选的地址, 区分两种原因:
  「查无此址」(接口零候选) 与 「有候选但全是 N」.
- **查询汇总** — 运行模式/命中率/失败数等口径.

### 响应结构说明

抓包文件里只有**请求**没有响应, 所以响应字段名是按请求体反推的
(`recogFlag` 来自需求描述, 其余来自 `getAddressDetail`/`getInstallInfo` 的请求体).
`src/cmhk_api.py` 因此采用**在任意层级找字段**的容错解析, 不假设固定嵌套路径:

- 候选地址: 找所有含 `recogFlag` 的对象; 没有该字段时退回找含 `value`+`carrierInfo` 的对象.
- 楼层/房间: 匹配 `floor*` / `flat*|room*|unit*` 这类 key, 同时兼容"楼层内嵌房间"和
  "楼层房间平铺"两种结构.

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
src/fetch_recog_y.py     串起来: 查询 -> 筛 recogFlag=Y -> 取楼层房间 -> 出表
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
  `extract_candidates` 的兜底条件.

## 五、已知边界

- **A002/A003/A006 是启发式提示, 不是结论**. 离线部分没有接入香港屋苑权威数据, 所以
  "疑似错别字""座号偏大""同族补全"都需人工或接口核实, 脚本不会自动改写任何地名用字.
- **接口响应结构是从请求体反推的**, 首次真机跑通后请核对 `output/recog_y/raw/` 并更新 fixture.
- **`笛卡尔积(推断)` 的楼层房间组合未经接口确认**, 用作测试用例前建议先过一遍 `getInstallInfo`.
- 反爬令牌会过期, 长列表建议分批跑(`--limit`), 或在令牌失效后重抓 curl 续跑.

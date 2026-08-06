# recogFlag=Y 的 Floor / Flat 提取报告

> **本次为 `plan` 模式, 不是真实接口数据.** live 模式跑法见 USAGE.md.

- **运行模式**: plan —— 仅生成查询计划, 未调用接口
- **查询词形态**: raw
- **输入地址数**: 31
- **已查询地址数**: 0 / 31
- **命中 recogFlag=Y 的地址数**: 0 / 31
- **未命中(bad case)数**: 未查询, 不适用
- **getInstallInfo 探测总次数**: 0
- **调用失败数**: 0

## 一、命中结果(输入地址 + Floor + Flat)

无


## 二、未命中的地址(bad case)

走完流程没有任何 Floor×Flat 组合返回 recogFlag=Y —— 这些就是要人工复核的 bad case.

无


## 三、说明

- 流程: searchAddress → busiResp.busiDataResp[0] → getAddressDetail → 遍历 Floor×Flat → getInstallInfo, 命中 recogFlag=Y 即记录并跳到下一个地址.
- `探测次数` 是该地址实际调用 getInstallInfo 的次数. 命中通常只需 1 次; 次数等于组合总数说明整栋楼都试遍了仍未命中.
- `离线筛查` 列是不依赖接口的静态规则结论(见 bad_case_report.md), 两边都判为问题的最值得优先看.

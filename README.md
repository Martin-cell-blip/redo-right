# 再做对 · Redo Right

> 一道错题不算“改过了”，直到它在隔天、一周后、三周后都被重新做对。

面向初高中强基 / 竞赛课程学生的**错题闭环工具**。功能刻意少：记错因、到期重做、三次做对才闭环。
不拍照搜题、不给答案、不生成新题——那些事别的产品已经做得很好，这个产品只负责把“错题本”从“抄下来”变成“再做对”。

## 为什么要有这个产品

竞赛生的错题本普遍存在三个问题：抄题太慢坚持不下来；只看答案不重做，隔周同类题照错；家长看见的是“错了 7 道题”，看不见“错在哪一类、改好了没”。
本工具的三个对应动作：

| 问题 | 动作 |
|---|---|
| 抄题慢 | 只记题目引用（“讲义 L3 例 4”）和错因编码，20 秒录一道 |
| 不重做 | 固定阶梯 1 / 7 / 21 天到期提醒，未到期不能重做，一天只算一次 |
| 看不见 | 每道题带错因编码，后续版本输出学生周报 / 家长周报 / 教研热力图 |

## 快速开始

```bash
python -m redo_right init --student 小明 --grade 高一
python -m redo_right causes                      # 看六类错因
python -m redo_right add --topic 数列-递推 --cause C2 --problem "讲义 L3 例 4" --note "没想到构造等比"
python -m redo_right due                         # 今天该重做什么
python -m redo_right redo <id> --pass            # 或 --fail，可加 --seconds 300
python -m redo_right list --status open
```

零第三方依赖；数据是一份 JSON（默认 `redo_right_data.json`），每次写入前自动备份最近 5 份。

## 错因表（教研可改）

`taxonomy/default_causes.json`，六类：C1 概念不清 / C2 思路未想到 / C3 计算失误 / C4 审题错误 / C5 时间不够 / C6 表达不规范。
每类带一句“重做前先做的一件事”和重做方式（先复述 / 限时 / 写满步骤）。改错因表时启动校验会拦住重复编码、缺字段、非法重做方式。

## 测试

```bash
python -m pytest tests -q
```

## 版本

见 [CHANGELOG.md](CHANGELOG.md)。每个版本记录触发原因、改动与验证方式。

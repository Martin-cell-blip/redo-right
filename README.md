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
python -m redo_right retag <id> --cause C2 --reason "其实是没想到换元"   # 累计两次做错后必须先做这步
python -m redo_right report --week-end 2026-10-12  # 学生周报（Markdown）
python -m redo_right export --out mistakes.csv     # 给教研/家长的 CSV
python -m redo_right parent --week-end 2026-10-12  # 家长周报（纯文字）
python -m redo_right sheet --on 2026-10-09 --out 重练单.html   # A4 可打印
python -m redo_right list --status open

# 班级：一个学生一份 JSON，放同一文件夹
python -m redo_right class --dir examples/demo_class heatmap --out 热力图.md
python -m redo_right class --dir examples/demo_class sheets --on 2026-10-05 --out-dir out/sheets
```

## 三个人各看一样东西

| 谁 | 看什么 | 命令 | 设计约束 |
|---|---|---|---|
| 学生 | 今天该重做什么；本周有没有变好 | `due` / `report` | 二次错的题单列，先归因再重做 |
| 家长 | 三个数字、一句错因、一件能帮的事 | `parent` | 不出现题目内容，不出现评价词 |
| 教研 / 老师 | 知识点 × 错因热力图；讲义该改哪一节；本周要看哪些题 | `class heatmap` | 知识点 ≥ 3 道且主导错因 ≥ 40% 才出建议 |

## 物料：重练单

重做发生在纸上。`sheet` / `class sheets` 生成 A4 重练单：每道题印“知识点 · 题目引用 · 重做前先做的一件事 · 作答区 · □对 □错 用时”，二次错的题变成归因单。
`class sheets` 同时产出**物料清单**：每页 3 道，按学生算页数、总页数、双面张数、无需打印名单，以及发放核对要求——缺页错发在工具里不留痕，必须在发放时拦住。

## AI 提效：只建议，不裁决

```bash
python -m redo_right suggest --problem "讲义 L3 例 4" --note "我当时没想到要先构造等比数列，第三步符号还错了"
# 建议 C2 思路未想到（high，来源 ai）
# 依据你写的：“没想到要先构造等比数列”
# 先回答这个问题再决定：你当时是不知道要构造等比数列，还是知道但没往这个方向想？
# 建议编号 s001。记错题时加 --suggestion s001，系统会记录你是否采纳。
python -m redo_right parent --ai            # 把家长周报措辞改口语；数字一个都不能变
python -m redo_right ai-stats               # AI 建议采纳率
```

| AI 做的事 | 合同（违约 → 重试一次 → 回退确定性版本） |
|---|---|
| 读学生原话，建议错因编码 | 编码在错因表内；依据是学生原话**逐字**引用；必须反问一句（问号结尾）；反问不得含解题内容 |
| 改写家长周报措辞 | 每个数字都必须在确定性事实里；不得出现评价词；≤320 字 |

AI 不改数字、不生成答案、不替学生选错因。`ai-stats` 的采纳率是衡量“AI 帮没帮上”的产品口径。
无 key 时所有命令照常工作（关键词规则 + 模板文本）。配置：`REDO_RIGHT_API_KEY`（或 `DEEPSEEK_API_KEY`）、`REDO_RIGHT_BASE_URL`（默认 https://api.deepseek.com）、`REDO_RIGHT_MODEL`（默认 deepseek-chat）。

## 演示数据

`examples/demo_class/` 是 **合成** 演示班级（12 人 × 3 周，`tools/make_demo_class.py`，seed 固定，只通过产品自己的 Service 生成，因此服从全部产品规则）。它用来演示热力图与物料清单长什么样，不是真实学生，不能作为效果证据。

## 两条写进代码的产品规则

1. **未到重做日不能重做，一天只算一次。** 间隔的意义就是不要当天再做一遍；连点两次也不会把阶梯刷上去。
2. **累计两次做错，先重新归因再重做。** 同一道题错两次是强信号：要么错因标错了，要么需要老师介入。系统把这道题从重做清单里拿出来单列，学生必须写一句“这次想清楚了错在哪”才能继续；同时打上老师关注标记，老师处理后写一句记录清除。

零第三方依赖；数据是一份 JSON（默认 `redo_right_data.json`），每次写入前自动备份最近 5 份。

```bash
python -m redo_right serve --port 8761      # 本地网页（手机优先；需要 fastapi + uvicorn）
python -m redo_right audit                  # 数据一致性检查：十条不变量
```

## 错因表（教研可改）

`taxonomy/default_causes.json`，六类：C1 概念不清 / C2 思路未想到 / C3 计算失误 / C4 审题错误 / C5 时间不够 / C6 表达不规范。
每类带一句“重做前先做的一件事”和重做方式（先复述 / 限时 / 写满步骤）。改错因表时启动校验会拦住重复编码、缺字段、非法重做方式。

## 稳定性

| 机制 | 说明 |
|---|---|
| 原子写 + 备份 | 临时文件 + `os.replace`，写前备份最近 5 份；断电不会留下半个 JSON |
| 模式迁移 | v0.1–v0.4 的文件（schema 1）在加载时原地升级到 schema 2；更新的文件拒绝打开并提示升级程序 |
| 不变量审计 | `audit` 跑十条规则（重做日期递增、fail_count 与记录一致、闭环必有连续通过、待归因必有两次失败、归因历史与当前一致……），任何文件随时可自检 |
| 一天一次 / 未到期不做 | 连点、刷阶梯、提前重做都被拒绝，不靠前端 |
| 压测 | `python tools/stress.py --students 300 --weeks 6`：全部走产品 Service 生成，再跑审计，并验证同 seed 双跑逐字节一致 |
| 无 key 可用 | AI 相关命令全部有确定性回退，测试全离线 |

## 测试

```bash
python -m pytest tests -q
```

## 指标与护栏

- **北极星：30 天闭环率**——记录满 30 天的错题中已三次做对的比例。它同时要求录入、到期重做、做对三件事都发生，刷任何一项都刷不上去。
- 每周看的输入指标：录题时长（自报）、到期重做完成率、二次错率、归因修改率（二次错后 from≠to）、家长周报送达率、教研采纳（讲义改动条数）、AI 建议采纳率、物料（清单页数 = 实印、错发 = 0）。
- 护栏：学生每周重做负担 ≤ 60 分钟；家长周报不出现题目与评价词（测试断言）；不以周报打开率放量。

## Pilot

v1.0 冻结功能。下一步不是加功能，是一个班四周的 Pilot，只记四件事、三问门槛——见 [PILOT.md](PILOT.md)。

## 版本

v1.0.0（2026-10-06）。六个版本每版一个触发，见 [CHANGELOG.md](CHANGELOG.md)：每个版本记录触发原因、改动与验证方式。

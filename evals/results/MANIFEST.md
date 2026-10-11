# 结果文件清单

`eval_suggest.py` 保存的 `label` 只含模式、提示词版本、切分与日期，不含模型与接口；模型信息以本清单为准（2026-10-11 补记）。所有 AI 运行 temperature = 0.2、response_format = json_object、`--split all`、`--workers 6`。

| 文件 | 模型 | 接口 | 合同 | 提示词 | 说明 |
|---|---|---|---|---|---|
| 2026-10-06_baseline.json | 关键词规则（无模型） | — | — | — | 无 key 时的回退方案 |
| 2026-10-06_ai_v1_old-contract.json | deepseek-chat | api.deepseek.com | 旧合同 | v1 | 第 2 轮"改之前" |
| 2026-10-06_ai_v1_new-contract.json / -r2 / -r3 | deepseek-chat | api.deepseek.com | 新合同 | v1 | 三轮 |
| 2026-10-06_ai_v2_new-contract.json / -r2 / -r3 | deepseek-chat | api.deepseek.com | 新合同 | v2 | 三轮，默认提示词 |
| 2026-10-10_ai_v2_deepseek-sameday.json | deepseek-chat | api.deepseek.com | 新合同 | v2 | 隔四天重跑一轮 |
| 2026-10-10_ai_v2_gpt-4.1-mini-r1 / -r2 / -r3.json | gpt-4.1-mini | api.openai.com/v1（REDO_RIGHT_BASE_URL / REDO_RIGHT_MODEL 覆盖） | 新合同 | v2 | 三轮 |
| uncertainty.json | — | — | — | — | `uncertainty.py` 输出 |
| agreement.json | — | — | — | — | `agreement.py` 输出（默认对照 2026-10-06_ai_v2_new-contract.json） |

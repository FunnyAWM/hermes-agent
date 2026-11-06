# 两端记忆比较样本（T-10）

依据 PRD §9.3。全部事实均为虚构，`home/` 是两端共用的数据根样本。
`questions.json` 固定十个问题，每题列出事实来源、应答事实（`expected`）、必须避免的结论
（`forbidden`，用于机器比对）与给人工看的说明（`reject`）。

样本按当前跨端契约构造：`MEMORY.md` 模型写入上限 3,000、`USER.md` 不限、根提示词位于 stable
首位、`notes/docs/` 为只读原文目录（`product-brief.md` 是样例，清单只出名称与摘要，正文需读取）。

## 采集

1. 运行 `.venv/Scripts/python.exe -X utf8 scripts/verify_memory_comparison.py`，确认样本能被当前内核
   读取，并记录输出的 SHA-256。
2. 把 `home/` 复制到两端的隔离数据根，保持文件字节一致；不要覆盖日常记忆。
3. 两端使用同一模型、参数与问题，每题开启新会话，不把上一题回答带入下一题。
4. Q08、Q09、Q10 要求读取笔记正文；仅看到清单摘要不算通过。Q03 / Q05 另有 `notes/docs/`
   原文来源，读取该目录只验可见性，不得改写原文。
5. 答案按 `answers/TEMPLATE.json` 的格式保存，每端一份（`answers/pc.json` /
   `answers/android.json`），填写 `platform`、`model`、`generated_at` 与十条答案。

## 比对

```bash
python scripts/verify_memory_comparison.py --answers evidence/fixtures/memory-comparison/answers/pc.json
python scripts/verify_memory_comparison.py --answers .../pc.json --json
```

- 退出码 0：十题都能在回答里找到全部 `expected`，且没有命中 `forbidden`；
- 退出码 1：缺事实或命中禁止结论，输出逐题明细；
- 退出码 3：答案文件缺少题号，采集未完成。

按事实含义判断，无需逐字匹配；答案同时含有正确事实和矛盾结论也判失败。没有记录的事应答
“未确定”或“不知道”，不得推测。输出措辞与长度不作一致性要求。如果模型不支持笔记读取，记录为
未验证，不用手动塞入正文冒充读取通过。

联调结果写入 `evidence/t10-<日期>.md`：记录两端版本、模型与参数、样本 SHA-256、每题两端原始
回答、事实是否矛盾及笔记读取记录。不得在报告中写入密钥。

当前仅验证样本和 PC 加载链路；Android 端 T-81 / T-88 未完成，尚无两端答案比较结果。

# T-03 改前证据

改前 `agent/system_prompt.py:785` 的拼装为：

`volatile_parts: list[str] = [skills_prompt, *_memory_parts(agent)]`

因此 volatile 段顺序是：技能索引 → MEMORY → USER → 插件段 → profile → 时间戳 → 环境。
这使技能索引插入了 PRD §7.5 要求的 SOUL → AGENTS → MEMORY → USER 连续段之前。

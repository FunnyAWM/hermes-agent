# T-03 改后证据

改后 `agent/system_prompt.py:785` 的拼装为：

`volatile_parts: list[str] = [*_memory_parts(agent), skills_prompt]`

因此 system prompt 的 volatile 段顺序变为：MEMORY → USER → 技能索引 → 插件段 → profile → 时间戳 → 环境。
稳定层与 context 层保持不变；`MemoryStore.load_from_disk()` 仍在 agent 初始化时生成冻结快照，符合缓存前缀不在会话中途重建的约束。

新增行为契约测试 `tests/agent/test_system_prompt.py::test_memory_blocks_precede_skills_in_volatile_prompt`，通过最小 agent 桩确认实际渲染顺序为 MEMORY → USER → SKILLS。

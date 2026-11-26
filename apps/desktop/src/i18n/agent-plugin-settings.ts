import type { AgentPluginRow } from '@/store/agent-plugins'

const fields: Record<string, [string, string]> = {
  message_sources: ['消息来源', '允许提交任务的网关渠道。["*"] 接受所有已配置渠道（含微信），[] 关闭消息接入。'],
  worker_provider: ['工作代理提供商', '新工作会话使用的模型提供商。可选择沿用当前配置档。'],
  worker_model: ['工作代理模型', '选择该提供商下的模型，或沿用当前配置档的默认模型。'],
  max_concurrent_workers: ['最大并发工作代理数', '路由器同时运行的工作代理数量。每个任务使用一个工作代理。'],
  workspace_root: ['工作区根目录', '用于为每个任务创建独立工作树的 Git 仓库绝对路径。'],
  default_sandbox: [
    '默认沙箱',
    'read-only 允许只读，workspace-write 允许写入工作区，workspace-exec 允许在工作区执行命令。'
  ],
  watchdog_seconds: ['无进展告警阈值', '任务或执行轮次的状态持续多少秒没有更新时发出告警。']
}

export function localizeAgentPlugin(row: AgentPluginRow, locale = 'en'): AgentPluginRow {
  if (row.source !== 'bundled' || row.key !== 'super_agent' || !locale.startsWith('zh')) {
    return row
  }

  const title = '超级代理'

  return {
    ...row,
    name: title,
    description: '由路由器分派任务，工作代理在独立会话中执行。你可以查看工作会话和执行结果。',
    settings_page: row.settings_page && { ...row.settings_page, title },
    settings_schema: row.settings_schema?.map(field => {
      const copy = fields[field.key]

      return copy ? { ...field, label: copy[0], description: copy[1] } : field
    })
  }
}

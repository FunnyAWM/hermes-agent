import type { AgentPluginRow } from '@/store/agent-plugins'

export const messageSourceLabels = {
  en: { all: 'All sources', local: 'Built-in bot', none: 'None', same: 'Same as read sources' },
  zh: { all: '全部来源', local: '内置机器人', none: '无', same: '沿用读取来源' }
}

const fields: Record<string, [string, string]> = {
  message_sources: ['读取消息来源', '选择可向 Super Agent 转交任务的渠道。「内置机器人」也包括桌面会话。选择「无」可关闭任务接收。'],
  write_message_sources: [
    '写入消息来源',
    '读写渠道在原会话接收每轮执行结果。仅写渠道在默认会话（Home Channel）接收。后台投递需要开启 allow_gateway_injection。选择「沿用读取来源」可同步读写范围。'
  ],
  worker_provider: ['工作代理提供商', '新工作会话使用的模型提供商。可选择沿用当前配置档。'],
  worker_model: ['工作代理模型', '选择该提供商下的模型，或沿用当前配置档的默认模型。'],
  max_concurrent_workers: ['最大并发工作代理数', '路由器同时运行的工作代理数量。每个任务使用一个工作代理。'],
  workspace_root: ['工作区根目录', '用于为每个任务创建独立工作树的 Git 仓库绝对路径。'],
  default_sandbox: [
    '默认沙箱',
    'read-only 允许只读，workspace-write 允许写入工作区，workspace-exec 允许在工作区执行命令。'
  ],
  watchdog_seconds: ['无进展告警阈值', '任务或执行轮次的状态持续多少秒没有更新时发出告警。'],
  turn_timeout_seconds: ['每回合硬时限（秒）', '0 表示关闭。该配置档下所有工作代理在开始新回合时采用此时限。'],
  history_retention_days: ['历史保留天数', '0 表示保留全部历史。设置正整数后，会清理已结案且投递已结算的过期票据，保留 Git 工作树。']
}

export function localizeAgentPlugin(row: AgentPluginRow, locale = 'en'): AgentPluginRow {
  if (row.source !== 'bundled' || row.key !== 'super_agent' || !locale.startsWith('zh')) {
    return row
  }

  const title = 'Super Agent'

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

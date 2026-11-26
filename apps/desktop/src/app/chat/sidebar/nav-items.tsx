import { Codicon } from '@/components/ui/codicon'

import { ARTIFACTS_ROUTE, CAPABILITIES_ROUTE, CRON_ROUTE, MEMORY_ROUTE, MESSAGING_ROUTE } from '../../routes'
import type { SidebarNavItem } from '../../types'

// A row's `tier` is the one mode it belongs to (Simple keeps the setup rows,
// Advanced adds the readouts); the list filters once, nothing is passed down.
export const SIDEBAR_NAV: SidebarNavItem[] = [
  {
    id: 'new-session',
    label: '',
    icon: props => <Codicon name="robot" {...props} />,
    action: 'new-session',
    keybindActionId: 'session.new'
  },
  {
    id: 'memory',
    label: '',
    icon: props => <Codicon name="book" {...props} />,
    route: MEMORY_ROUTE,
    keybindActionId: 'nav.memory'
  },
  {
    id: 'capabilities',
    label: '',
    icon: props => <Codicon name="symbol-misc" {...props} />,
    route: CAPABILITIES_ROUTE,
    keybindActionId: 'nav.capabilities'
  },
  {
    id: 'messaging',
    label: '',
    icon: props => <Codicon name="comment" {...props} />,
    route: MESSAGING_ROUTE,
    keybindActionId: 'nav.messaging'
  },
  // Artifacts and Scheduled jobs are outputs of running Kouri Harness the developer
  // way; Capabilities and Messaging are how anyone sets it up.
  {
    id: 'artifacts',
    label: '',
    icon: props => <Codicon name="files" {...props} />,
    route: ARTIFACTS_ROUTE,
    keybindActionId: 'nav.artifacts',
    tier: 'advanced'
  },
  {
    id: 'cron',
    label: '',
    icon: props => <Codicon name="watch" {...props} />,
    route: CRON_ROUTE,
    keybindActionId: 'nav.cron',
    tier: 'advanced'
  }
]

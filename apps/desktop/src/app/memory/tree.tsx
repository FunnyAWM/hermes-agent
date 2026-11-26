import { useMemo, useState } from 'react'

import type { MemoryNoteEntry } from '@/api/memory'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { NoteNode } from './types'

/**
 * Group `notes/<category>/<name>.md` into folders instead of one flat list.
 *
 * The tree mirrors the filesystem exactly — a note two levels deep shows two folders —
 * so what a user sees here is what `read_file notes/…` resolves to. Folders sort before
 * files at every level; within a level, names sort the way a file browser would.
 */
export function buildNoteTree(entries: MemoryNoteEntry[]): NoteNode[] {
  const root: NoteNode = { name: '', path: '', children: [] }

  for (const entry of entries) {
    const segments = entry.path.replace(/^notes\//, '').split('/')
    let cursor = root
    let prefix = 'notes'
    segments.forEach((segment, index) => {
      prefix = `${prefix}/${segment}`
      const isLeaf = index === segments.length - 1
      let child = cursor.children.find(node => node.name === segment && Boolean(node.entry) === isLeaf)

      if (!child) {
        child = { name: segment, path: prefix, children: [], entry: isLeaf ? entry : undefined }
        cursor.children.push(child)
      }

      cursor = child
    })
  }

  const sort = (nodes: NoteNode[]): NoteNode[] => {
    nodes.sort((left, right) => {
      const leftFolder = left.entry ? 1 : 0
      const rightFolder = right.entry ? 1 : 0

      return leftFolder - rightFolder || left.name.localeCompare(right.name)
    })
    nodes.forEach(node => sort(node.children))

    return nodes
  }

  return sort(root.children)
}

export function MemoryTree({
  entries,
  onSelect,
  selectionPath
}: {
  entries: MemoryNoteEntry[]
  onSelect: (path: string) => void
  selectionPath: string | null
}) {
  const tree = useMemo(() => buildNoteTree(entries), [entries])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const isOpen = (path: string) => open[path] !== false

  return (
    <>
      {tree.map(node => (
        <TreeNode
          depth={0}
          isOpen={isOpen}
          key={node.path}
          node={node}
          onSelect={onSelect}
          onToggle={path => setOpen(current => ({ ...current, [path]: !isOpen(path) }))}
          selectionPath={selectionPath}
        />
      ))}
    </>
  )
}

function TreeNode({
  depth,
  isOpen,
  node,
  onSelect,
  onToggle,
  selectionPath
}: {
  depth: number
  isOpen: (path: string) => boolean
  node: NoteNode
  onSelect: (path: string) => void
  onToggle: (path: string) => void
  selectionPath: string | null
}) {
  const { t } = useI18n()
  const m = t.memoryPanel
  const indent = { paddingLeft: `${12 + depth * 12}px` }

  if (node.entry) {
    return (
      <button
        className={cn(
          'hover:bg-(--ui-control-hover-background) flex w-full items-center justify-between gap-2 py-1 pr-3 text-left',
          selectionPath === node.path && 'bg-(--ui-control-active-background)'
        )}
        onClick={() => onSelect(node.path)}
        style={indent}
        type="button"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <Codicon className="text-(--ui-text-tertiary) shrink-0 text-[11px]" name="markdown" />
          <span className="truncate font-mono text-xs">{node.name.replace(/\.md$/, '')}</span>
          {!node.entry.editable && (
            <Codicon className="text-(--ui-text-tertiary) shrink-0 text-[10px]" name="lock" />
          )}
        </span>
        <span className="text-(--ui-text-tertiary) shrink-0 text-[10px]">{m.bytes(node.entry.bytes)}</span>
      </button>
    )
  }

  const expanded = isOpen(node.path)

  return (
    <div>
      <button
        className="hover:bg-(--ui-control-hover-background) flex w-full items-center gap-1 py-1 pr-3 text-left"
        onClick={() => onToggle(node.path)}
        style={indent}
        type="button"
      >
        <Codicon
          className="text-(--ui-text-tertiary) shrink-0 text-[10px]"
          name={expanded ? 'chevron-down' : 'chevron-right'}
        />
        <Codicon className="text-(--ui-text-tertiary) shrink-0 text-[11px]" name="folder" />
        <span className="truncate text-xs">{node.name}</span>
        <span className="text-(--ui-text-tertiary) ml-auto shrink-0 text-[10px]">{node.children.length}</span>
      </button>
      {expanded &&
        node.children.map(child => (
          <TreeNode
            depth={depth + 1}
            isOpen={isOpen}
            key={child.path}
            node={child}
            onSelect={onSelect}
            onToggle={onToggle}
            selectionPath={selectionPath}
          />
        ))}
    </div>
  )
}

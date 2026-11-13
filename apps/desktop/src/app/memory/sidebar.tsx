import { cn } from '@/lib/utils'

/** One context file in the left column. The dot marks unsaved edits on the open file. */
export function DocRow({
  active,
  dirty,
  label,
  meta,
  onSelect
}: {
  active: boolean
  dirty: boolean
  label: string
  meta: string
  onSelect: () => void
}) {
  return (
    <button
      className={cn(
        'hover:bg-(--ui-control-hover-background) flex w-full items-center justify-between gap-2 px-3 py-1 text-left',
        active && 'bg-(--ui-control-active-background)'
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="truncate font-mono text-xs">{label}</span>
        {dirty && <span aria-hidden className="bg-(--ui-text-tertiary) size-1.5 shrink-0 rounded-full" />}
      </span>
      <span className="text-(--ui-text-tertiary) shrink-0 text-[10px]">{meta}</span>
    </button>
  )
}

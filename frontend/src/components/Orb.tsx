import type { Status } from '../portal/state'

interface OrbProps {
  status: Status
  waiting: boolean
  active: boolean
  label: string
  onToggle: () => void
}

/** CoO's animated presence. Tap to start or stop talking; the animation shows the state. */
export function Orb({ status, waiting, active, label, onToggle }: OrbProps) {
  return (
    <button
      type="button"
      className="orb"
      data-state={status}
      data-waiting={waiting || undefined}
      aria-pressed={active}
      aria-label={active ? `Stop talking. ${label}` : `Start talking. ${label}`}
      onClick={onToggle}
    >
      <span className="orb-ring" />
      <span className="orb-ring orb-ring--late" />
      <span className="orb-core" />
      <span className="orb-bars" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
        <i />
      </span>
    </button>
  )
}

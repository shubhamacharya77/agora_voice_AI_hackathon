import { useId } from 'react'

// CoO's character: a soft, hooded plush buddy. Its pose and face show what CoO is doing;
// all motion is CSS (see .buddy in styles.css), driven by data attributes.

export type Mood = 'idle' | 'listening' | 'thinking' | 'speaking' | 'sad'

interface BuddyProps {
  mood: Mood
  waving?: boolean // greeting
  curious?: boolean // a draft is waiting for the user's answer
  active?: boolean // a conversation is live
  label?: string
  onTap?: () => void
}

const INK = '#2d2236'

export function Buddy({ mood, waving, curious, active, label, onTap }: BuddyProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const id = (name: string) => `${uid}-${name}`
  const url = (name: string) => `url(#${id(name)})`

  const art = (
    <svg className="buddy-art" viewBox="-20 0 280 280" aria-hidden="true">
      <defs>
        <radialGradient id={id('skin')} cx="36%" cy="26%" r="82%">
          <stop offset="0" stopColor="#ffe9de" />
          <stop offset=".55" stopColor="#f9cdb8" />
          <stop offset="1" stopColor="#e9a68c" />
        </radialGradient>
        <radialGradient id={id('limb')} cx="35%" cy="22%" r="95%">
          <stop offset="0" stopColor="#ffe4d6" />
          <stop offset="1" stopColor="#e8a389" />
        </radialGradient>
        <radialGradient id={id('face')} cx="50%" cy="40%" r="68%">
          <stop offset="0" stopColor="#fff4ee" />
          <stop offset="1" stopColor="#f7d2bf" />
        </radialGradient>
        <linearGradient id={id('inset')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#d48b70" stopOpacity=".5" />
          <stop offset=".38" stopColor="#d48b70" stopOpacity="0" />
        </linearGradient>
        <radialGradient id={id('blush')}>
          <stop offset="0" stopColor="#f593a6" stopOpacity=".8" />
          <stop offset="1" stopColor="#f593a6" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={id('shadow')}>
          <stop offset="0" stopColor="#5b3fa8" stopOpacity=".34" />
          <stop offset="1" stopColor="#5b3fa8" stopOpacity="0" />
        </radialGradient>
      </defs>

      <ellipse className="b-shadow" cx="120" cy="262" rx="74" ry="12" fill={url('shadow')} />

      <g className="b-figure">
        <rect x="86" y="208" width="30" height="48" rx="15" fill={url('limb')} />
        <rect x="124" y="208" width="30" height="48" rx="15" fill={url('limb')} />
        <path
          d="M120 112C168 112 187 150 189 190C191 228 164 244 120 244C76 244 49 228 51 190C53 150 72 112 120 112Z"
          fill={url('skin')}
        />

        <g className="b-head">
          <ellipse cx="120" cy="96" rx="80" ry="72" fill={url('skin')} />
          <ellipse cx="90" cy="52" rx="28" ry="13" fill="#fff" opacity=".38" transform="rotate(-24 90 52)" />
          {/* the hood's opening */}
          <ellipse cx="120" cy="104" rx="58" ry="50" fill="#fde3d6" />
          <ellipse cx="120" cy="106" rx="51" ry="43" fill={url('face')} />
          <ellipse cx="120" cy="106" rx="51" ry="43" fill={url('inset')} />
          <ellipse cx="88" cy="121" rx="13" ry="7.5" fill={url('blush')} />
          <ellipse cx="152" cy="121" rx="13" ry="7.5" fill={url('blush')} />

          <g className="b-eyes">
            <g className="b-eyes-open">
              <ellipse cx="102" cy="103" rx="6.2" ry="7.6" fill={INK} />
              <ellipse cx="138" cy="103" rx="6.2" ry="7.6" fill={INK} />
              <circle cx="104.2" cy="99.6" r="2.3" fill="#fff" />
              <circle cx="140.2" cy="99.6" r="2.3" fill="#fff" />
            </g>
            <g className="b-eyes-closed" fill="none" stroke={INK} strokeWidth="3" strokeLinecap="round">
              <path d="M96 102Q102 108 108 102" />
              <path d="M132 102Q138 108 144 102" />
            </g>
          </g>
          <path
            className="b-smile"
            d="M112 119Q120 127 128 119"
            fill="none"
            stroke={INK}
            strokeWidth="3"
            strokeLinecap="round"
          />
          <ellipse className="b-mouth" cx="120" cy="122" rx="6.5" ry="5.5" fill="#8c3f55" />
        </g>

        <g className="b-arm b-arm--left">
          <rect x="40" y="146" width="28" height="66" rx="14" fill={url('limb')} />
        </g>
        <g className="b-arm b-arm--right">
          <rect x="172" y="146" width="28" height="66" rx="14" fill={url('limb')} />
        </g>
      </g>

      <g className="b-dots">
        <circle cx="200" cy="44" r="4.5" />
        <circle cx="214" cy="29" r="6" />
        <circle cx="231" cy="12" r="8" />
      </g>
    </svg>
  )

  const data = {
    'data-mood': mood,
    'data-waving': waving || undefined,
    'data-curious': curious || undefined,
    'data-active': active || undefined,
  }
  if (!onTap) {
    return (
      <div className="buddy" {...data}>
        {art}
      </div>
    )
  }
  return (
    <button type="button" className="buddy" {...data} aria-label={label} aria-pressed={active} onClick={onTap}>
      {art}
    </button>
  )
}

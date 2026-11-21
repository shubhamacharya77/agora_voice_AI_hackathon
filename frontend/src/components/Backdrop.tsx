/** The soft lavender-to-pink waves behind everything. Decorative only. */
export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <svg viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
        <path
          className="wave wave--1"
          d="M-80 640C180 520 360 700 620 640S1060 420 1520 520V980H-80Z"
          fill="#f6c6da"
          opacity=".75"
        />
        <path
          className="wave wave--2"
          d="M-80 760C240 660 520 860 860 760S1280 620 1520 700V980H-80Z"
          fill="#fbd8e5"
          opacity=".85"
        />
        <path
          className="wave wave--3"
          d="M820-40C1000 80 1060 220 1240 260S1480 240 1540 200V-60Z"
          fill="#d6c6f7"
          opacity=".7"
        />
        <path
          className="wave wave--4"
          d="M-60 120C120 40 260 200 420 150S640-20 700-60H-60Z"
          fill="#e7d6f6"
          opacity=".55"
        />
      </svg>
    </div>
  )
}

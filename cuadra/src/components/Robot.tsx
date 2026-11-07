export type RobotMood = 'happy' | 'neutral' | 'worried' | 'working'

const MOUTHS: Record<RobotMood, string> = {
  happy: 'M11 22 C14 27 26 27 29 22',
  neutral: 'M13 24 L27 24',
  worried: 'M12 26 C15 21 25 21 28 26',
  working: 'M13 24 L16 24 M19 24 L22 24 M25 24 L28 24',
}

type RobotProps = { mood: RobotMood; size?: number; label?: string }

/** The one piece of character in the product: its face reads whether a movement adds up. */
export function Robot({ mood, size = 40, label }: RobotProps) {
  const eyeHeight = mood === 'happy' ? 3 : 6
  const eyeY = mood === 'happy' ? 14 : 12
  return (
    <svg
      className={`robot robot-${mood}`}
      width={size}
      height={size}
      viewBox="0 0 40 40"
      role={label ? 'img' : 'presentation'}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <rect x="19" y="0" width="2" height="4" fill="currentColor" />
      <rect x="1" y="4" width="38" height="35" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect className="robot-eye" x="10" y={eyeY} width="6" height={eyeHeight} fill="currentColor" />
      <rect className="robot-eye" x="24" y={eyeY} width="6" height={eyeHeight} fill="currentColor" />
      <path d={MOUTHS[mood]} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" />
    </svg>
  )
}

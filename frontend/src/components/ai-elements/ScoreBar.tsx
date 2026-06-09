interface ScoreBarProps {
  label: string
  value: number
  max?: number
  format?: 'percent' | 'raw'
  colorFn?: (v: number) => string
}

function defaultColor(v: number): string {
  if (v < 0.35) return '#34d399'
  if (v < 0.65) return '#fbbf24'
  return '#f87171'
}

export function ScoreBar({ label, value, max = 1, format = 'percent', colorFn = defaultColor }: ScoreBarProps) {
  const ratio = Math.min(value / max, 1)
  const color = colorFn(ratio)
  const display = format === 'percent' ? (ratio * 100).toFixed(0) + '%' : String(value)

  return (
    <div className="ai-score-bar">
      <div className="ai-score-bar-header">
        <span className="ai-score-bar-label">{label}</span>
        <span className="ai-score-bar-value" style={{ color }}>{display}</span>
      </div>
      <div className="ai-score-bar-track">
        <div
          className="ai-score-bar-fill"
          style={{ width: `${ratio * 100}%`, background: color }}
          role="progressbar"
          aria-valuenow={Math.round(ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
    </div>
  )
}

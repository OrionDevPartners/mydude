import { useState } from 'react'
import { Copy, Check, Code2 } from 'lucide-react'

interface CodeBlockProps {
  code: string
  language?: string
  title?: string
}

export function CodeBlock({ code, language, title }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { /* ignore */ }
  }

  return (
    <div className="ai-code-block">
      <div className="ai-code-header">
        <span className="ai-code-lang">
          <Code2 size={11} />
          {title ?? language ?? 'code'}
        </span>
        <button
          className="btn btn-ghost btn-sm ai-code-copy"
          onClick={handleCopy}
          aria-label="Copy code"
          type="button"
        >
          {copied ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy</>}
        </button>
      </div>
      <pre className="ai-code-pre"><code>{code}</code></pre>
    </div>
  )
}

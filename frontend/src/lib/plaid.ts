// Plaid Link loader.
//
// CDN EXCEPTION (documented): replit.md mandates "no external CDN dependencies",
// but Plaid REQUIRES that Link be loaded from their own origin
// (https://cdn.plaid.com/link/v2/stable/link-initialize.js) — it cannot be
// vendored, bundled, or self-hosted per Plaid's integration terms. The architect
// signed off on this single, scoped exception. The script is loaded lazily, only
// when the operator clicks "Connect bank", so nothing third-party loads on a
// normal page view.

const PLAID_SRC = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'

interface PlaidLinkMetadata {
  institution?: { name?: string | null; institution_id?: string | null } | null
}

interface PlaidLinkHandler {
  open: () => void
  exit: (opts?: { force?: boolean }) => void
  destroy: () => void
}

interface PlaidLinkOptions {
  token: string
  onSuccess: (publicToken: string, metadata: PlaidLinkMetadata) => void
  onExit?: (err: { display_message?: string; error_message?: string } | null) => void
  onEvent?: (eventName: string) => void
}

interface PlaidGlobal {
  create: (opts: PlaidLinkOptions) => PlaidLinkHandler
}

declare global {
  interface Window {
    Plaid?: PlaidGlobal
  }
}

let loadPromise: Promise<PlaidGlobal> | null = null

// Lazily inject the Plaid Link script and resolve once window.Plaid is ready.
// Fails loud (rejects) if the script cannot be loaded — no silent fallback.
function loadPlaid(): Promise<PlaidGlobal> {
  if (window.Plaid) return Promise.resolve(window.Plaid)
  if (loadPromise) return loadPromise

  loadPromise = new Promise<PlaidGlobal>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${PLAID_SRC}"]`)
    const onReady = () => {
      if (window.Plaid) resolve(window.Plaid)
      else reject(new Error('Plaid Link script loaded but window.Plaid is unavailable.'))
    }
    if (existing) {
      existing.addEventListener('load', onReady)
      existing.addEventListener('error', () => reject(new Error('Failed to load Plaid Link script.')))
      return
    }
    const script = document.createElement('script')
    script.src = PLAID_SRC
    script.async = true
    script.onload = onReady
    script.onerror = () => {
      loadPromise = null // allow a retry on the next click
      reject(new Error('Failed to load Plaid Link script.'))
    }
    document.head.appendChild(script)
  })
  return loadPromise
}

export interface PlaidExchangePayload {
  public_token: string
  institution_name?: string
  institution_id?: string
}

// Open Plaid Link with a server-issued link_token. Resolves with the public
// token + institution metadata when the user completes the flow, resolves null
// if they close it without finishing, and rejects on a hard error.
export async function openPlaidLink(linkToken: string): Promise<PlaidExchangePayload | null> {
  const Plaid = await loadPlaid()
  return new Promise<PlaidExchangePayload | null>((resolve, reject) => {
    let settled = false
    const handler = Plaid.create({
      token: linkToken,
      onSuccess: (publicToken, metadata) => {
        settled = true
        resolve({
          public_token: publicToken,
          institution_name: metadata.institution?.name ?? undefined,
          institution_id: metadata.institution?.institution_id ?? undefined,
        })
        handler.destroy()
      },
      onExit: (err) => {
        if (settled) return
        if (err && (err.display_message || err.error_message)) {
          reject(new Error(err.display_message || err.error_message || 'Plaid Link error.'))
        } else {
          resolve(null) // user closed without an error
        }
        handler.destroy()
      },
    })
    handler.open()
  })
}

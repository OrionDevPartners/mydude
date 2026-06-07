# Live Cancellation Verification Runbook

**Goal:** drive ONE real, end-to-end subscription cancellation by hand (and 2–3
real login/account-view checks) so the cancel button labels in
`DEFAULT_CANCEL_TEXTS` and the selectors in `_do_cancel` can be tuned to what
real provider pages actually say — instead of educated guesses.

This is the human-only step. The agent cannot create paid accounts, enter
payment details, or solve CAPTCHAs, so you run the flow and record what you see;
the recorded labels then feed a follow-up code-tuning pass.

> Safety: cancellation is two-phase. **Request cancel** only logs in and shows
> the account page (no irreversible action, status → `cancel_pending`).
> **Confirm cancel** is the only irreversible step, and it refuses to run unless
> you first requested and then typed the literal word `CANCEL`. You stay in
> control the whole way.

---

## 1. Prerequisites (one-time setup)

### 1a. A cloud browser (Browserbase)
Local Chromium can't launch in this container, so MyDude drives a remote
Browserbase session. Set these as **Secrets** (Tools → Secrets):

- `BROWSERBASE_API_KEY` — from your Browserbase dashboard
- `BROWSERBASE_PROJECT_ID` — from your Browserbase dashboard

### 1b. Enable the browser capability + allow-list the providers
Set these as Secrets / environment variables:

- `ENABLE_BROWSER_CAPABILITY` = `true`
- `BROWSER_ALLOWED_DOMAINS` = comma-separated bare domains for the providers you
  will test. Use the **registrable** domain only — subdomains (e.g.
  `accounts.spotify.com`, `auth.hulu.com`) are automatically covered.

  Recommended throwaway-friendly set (pick 2–3 you actually sign up for):
  ```
  netflix.com,spotify.com,hulu.com,disneyplus.com,max.com,amazon.com,youtube.com,google.com
  ```
  Notes:
  - YouTube Premium logs in via `accounts.google.com`, so include **both**
    `google.com` and `youtube.com`.
  - Hulu uses `auth.hulu.com` + `secure.hulu.com` — `hulu.com` covers both.
  - If a login redirects to a domain you didn't list, the run is blocked with a
    clear "Domain 'X' is not in the browse allow-list" message. Add that domain
    and retry — and **note it in the report below**, that's useful signal.

Restart the app after changing secrets so the new values load.

### 1c. Throwaway paid accounts
Create 2–3 disposable paid accounts on allow-listed providers, each with:
- A real (but disposable) payment method.
- An email you can read codes from, OR a phone for SMS codes.
- Verified past any email/CAPTCHA sign-up checks.

Pick at least ONE you are happy to actually cancel for the full end-to-end run.

> OTP note: MyDude can auto-pull **SMS** codes via the bridge, but **cannot**
> read authenticator-app codes. If a provider forces app-based 2FA, either
> disable it on the throwaway account or be ready to enter the code in the live
> Browserbase session manually.

---

## 2. Run the flow in the UI

All steps happen on the **Subscriptions** page (`/subscriptions`).

1. **Add the subscription.** Use "Add manually" (or run Discover). If you pick a
   known provider by domain, login/account URLs auto-fill from the catalog; for
   anything else fill `login_url` and `account_url` yourself.
2. **Save credentials.** Open the subscription, enter the account `username` and
   `password`, and Save. The password is encrypted into the vault — never stored
   in plaintext on the subscription.
3. **Confirm status.** Make sure the subscription status is `confirmed`
   (browser actions are refused for `candidate`/`dismissed`).
4. **Open account (read-only).** Click **Open account**. This validates
   login + reaching the account/billing page. Confirm the screenshot shows the
   logged-in account page. ➜ record result for EACH provider in §3a.
5. **Request cancel.** Click **Request cancel**. Status flips to
   `cancel_pending` and you get the account page + screenshot to review. No
   cancellation has happened yet. ➜ record the page you land on in §3b.
6. **Confirm cancel (irreversible).** Type `CANCEL` in the confirmation box and
   submit. This runs the real cancel/confirm clicks. ➜ record the exact labels
   and outcome in §3b.

If MyDude reports "Couldn't find a cancel control automatically", the account
page is shown so you can finish by hand — **that's exactly the case we want to
capture**: write down every button label on those pages in §3b.

---

## 3. Observation report — fill this in

The agent will use this verbatim to tune `DEFAULT_CANCEL_TEXTS` and `_do_cancel`.
Copy the **exact on-screen text** (capitalisation, punctuation, "&", etc.), and
note whether each control was a real button or a link/other element.

### 3a. Login + account-view checks (do 2–3 providers)

| Provider | Login OK? | Account page reached? | Extra notes (redirect domains hit, 2FA type, SPA slow to load?) |
|----------|-----------|------------------------|----------------------------------------------------------------|
|          |           |                        |                                                                |
|          |           |                        |                                                                |
|          |           |                        |                                                                |

### 3b. Full end-to-end cancel (the one disposable account)

- **Provider:**
- **Did MyDude click through automatically, or did you finish by hand?**
- **Was an extra settle/wait needed before the cancel control appeared? (roughly how long?)**

Record each step's control text in order (initiate → progress → confirm):

| Step (initiate / progress / confirm) | Exact on-screen label | Element type (button / link / other) | Page/URL it was on |
|--------------------------------------|-----------------------|--------------------------------------|--------------------|
|                                      |                       |                                      |                    |
|                                      |                       |                                      |                    |
|                                      |                       |                                      |                    |
|                                      |                       |                                      |                    |

- **Any retention/"are you sure?" interstitials, surveys, or "pause instead"
  upsells in the way? Copy their button labels too:**
- **Final confirmation text shown after success (e.g. "Your membership ends on…"):**
- **Screenshots:** attach the Request-cancel and Confirm-cancel screenshots.

### 3c. Anything surprising
- (e.g. needed a domain not in the allow-list, OTP couldn't be read, a label
  that's close to but not in the current list, an iframe, etc.)

---

## 4. What happens next

Hand §3 back to the agent. With the **observed** labels it will:
- add/adjust entries in `DEFAULT_CANCEL_TEXTS` (`src/browser/backends.py`),
- tune the `_do_cancel` selectors / settle-wait if SPA pages needed it,
- extend `tests/test_subscription_cancel.py` with the now-known real labels.

Until that real run is reported, the cancel labels remain educated guesses —
this runbook is the bridge from guess to observed truth.

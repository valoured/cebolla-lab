// rebuild trigger
/**
 * Trigger a GitHub Actions workflow dispatch from the browser.
 *
 * Used by the "REFRESH DATA" button to pull fresh lineups/odds/projections
 * on demand instead of waiting for the next cron tick.
 *
 * Requires a fine-grained Personal Access Token (PAT) with "Actions: write"
 * permission on the cebolla-lab repo. Stored as VITE_GH_DISPATCH_TOKEN env var
 * in Cloudflare Pages settings — Vite inlines it at build time.
 */

const GH_REPO  = 'valoured/cebolla-lab'
const WORKFLOW = 'daily-pulls.yml'
const TOKEN    = import.meta.env.VITE_GH_DISPATCH_TOKEN

export async function triggerWorkflow(job) {
  if (!TOKEN) {
    return { ok: false, error: 'No dispatch token configured' }
  }

  const url = 'https://api.github.com/repos/' + GH_REPO + '/actions/workflows/' + WORKFLOW + '/dispatches'

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + TOKEN,
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: { job: job || 'all' }
      })
    })

    if (res.status === 204) {
      return { ok: true }
    }

    const errText = await res.text()
    return { ok: false, error: res.status + ': ' + errText.slice(0, 100) }
  } catch (e) {
    return { ok: false, error: e.message || String(e) }
  }
}

export async function refreshSlate() {
  const results = await Promise.all([
    triggerWorkflow('lineups'),
    triggerWorkflow('dk_odds'),
    triggerWorkflow('scores')
  ])

  const failed = results.filter(function (r) { return !r.ok })
  if (failed.length) {
    return { ok: false, error: failed[0].error }
  }

  await new Promise(function (r) { setTimeout(r, 2000) })
  return await triggerWorkflow('projections')
}

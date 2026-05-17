/**
 * Trigger a GitHub Actions workflow dispatch from the browser.
 *
 * Used by the "REFRESH DATA" button to pull fresh lineups/odds/projections
 * on demand instead of waiting for the next cron tick.
 *
 * Requires a fine-grained Personal Access Token (PAT) with "Actions: write"
 * permission on the cebolla-lab repo. Store it in `.env.local` as
 * VITE_GH_DISPATCH_TOKEN — Vite will inline it at build time.
 *
 * Security note: this PAT is shipped to the browser. Since the repo is
 * public and only Valoured uses this site, exposure risk is acceptable.
 * If multi-user, this would need to move server-side.
 */

const GH_REPO  = 'valoured/cebolla-lab'
const WORKFLOW = 'daily-pulls.yml'
const TOKEN    = import.meta.env.VITE_GH_DISPATCH_TOKEN

/**
 * Trigger the workflow with a specific job input.
 * @param {string} job — 'all', 'lineups', 'dk_odds', 'projections', 'scores', etc.
 * @returns {Promise<{ok: boolean, error?: string}>}
 */
export async function triggerWorkflow(job = 'all') {
  if (!TOKEN) {
    return { ok: false, error: 'No dispatch token configured' }
  }

  const url = `https://api.github.com/repos/${GH_REPO}/actions/workflows/${WORKFLOW}/dispatches`
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/vnd.github+json',
        'Authorization': `Bearer ${TOKEN}`,
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: { job },
      }),
    })

    if (res.status === 204) {
      return { ok: true }
    }

    const errText = await res.text()
    return { ok: false, error: `${res.status}: ${errText.slice(0, 100)}` }
  } catch (e) {
    return { ok: false, error: e.message || String(e) }
  }
}

/**
 * Trigger the lineups → odds → projections chain.
 * Returns immediately; the actual jobs take 20-90s to complete on GH side.
 */
export async function refreshSlate() {
  // 'all' triggers everything but the heavy nightly jobs are gated by cron
  // and won't fire on workflow_dispatch unless explicitly selected.
  // We use a sequence: lineups + odds + scores in parallel, projections needs them.
  const results = await Promise.all([
    triggerWorkflow('lineups'),
    triggerWorkflow('dk_odds'),
    triggerWorkflow('scores'),
  ])
  const failed = results.filter(r => !r.ok)
  if (failed.length) {
    return { ok: false, error: failed[0].error }
  }

  // Wait a beat for those to start, then kick projections
  await new Promise(r => setTimeout(r, 2000))
  return await triggerWorkflow('projections')
}

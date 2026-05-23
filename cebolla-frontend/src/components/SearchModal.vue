<script setup>
/**
 * SearchModal.vue — Global player + team search.
 *
 * Triggered globally via Ctrl+K (Cmd+K on Mac) or `/`. Mounted in App.vue.
 *
 * Two result sections:
 *   - PLAYERS — name match, shows team abbrev + position
 *   - TEAMS   — abbrev or name match
 *
 * Keyboard nav:
 *   ↑/↓     walk results
 *   Enter   select highlighted
 *   Esc     close
 *
 * Implementation notes:
 *   - Renders via <Teleport to="body"> so it escapes any stacking context.
 *   - Query is debounced (250ms) to avoid hammering Supabase on each keystroke.
 *   - Results are sectioned but indexed in a single flat array so ↑↓
 *     can walk seamlessly across the boundary.
 *   - When PR #4 (team deep dives) ships, change the team-result handler
 *     to route to `/team/:abbrev` instead of the slate filter URL.
 */

import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { supabase } from '../supabase.js'
import { useFavorites } from '../composables/useFavorites.js'
import FavoriteStar from './FavoriteStar.vue'

const router = useRouter()
const { playerList: favPlayers, teamList: favTeams, totalCount: favCount } = useFavorites()

const isOpen = ref(false)
const query = ref('')
const players = ref([])
const teams = ref([])
const loading = ref(false)
const highlightIndex = ref(0)

const inputRef = ref(null)
let debounceTimer = null

// ── Open / close ────────────────────────────────────────────────
// Body scroll lock prevents the background page from scrolling while the
// modal is open. Without this, mobile users dragging on the dim overlay
// area scroll the page underneath, which is jarring. We save the previous
// overflow value to restore exactly what was there before — Tailwind or
// other code may have set it on the body for other reasons.
let previousBodyOverflow = ''

function lockBodyScroll() {
  if (typeof document === 'undefined') return
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
}
function unlockBodyScroll() {
  if (typeof document === 'undefined') return
  document.body.style.overflow = previousBodyOverflow
  previousBodyOverflow = ''
}

function open() {
  isOpen.value = true
  query.value = ''
  players.value = []
  teams.value = []
  highlightIndex.value = 0
  lockBodyScroll()
  nextTick(() => {
    inputRef.value?.focus()
  })
}
function close() {
  isOpen.value = false
  if (debounceTimer) clearTimeout(debounceTimer)
  unlockBodyScroll()
}

// ── Global hotkey ───────────────────────────────────────────────
function isTypingInField(e) {
  const t = e.target
  if (!t) return false
  const tag = t.tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if (t.isContentEditable) return true
  return false
}

function handleGlobalKey(e) {
  // Ctrl/Cmd + K opens regardless of focus
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    isOpen.value ? close() : open()
    return
  }
  // `/` opens, but only when not already typing somewhere else
  if (e.key === '/' && !isOpen.value && !isTypingInField(e)) {
    e.preventDefault()
    open()
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleGlobalKey)
  // Expose a tiny global so other components (TopNav button, etc) can
  // trigger the modal without prop-drilling or a Vue event bus.
  window.__openCebollaSearch = open
})
onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKey)
  if (debounceTimer) clearTimeout(debounceTimer)
  if (window.__openCebollaSearch === open) {
    delete window.__openCebollaSearch
  }
  // Defensive: if the modal happens to be open at unmount time (e.g.
  // route change during search), make sure we don't leave the body
  // permanently scroll-locked.
  if (isOpen.value) unlockBodyScroll()
})

// ── Search ──────────────────────────────────────────────────────
watch(query, (q) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!q || q.trim().length < 2) {
    players.value = []
    teams.value = []
    loading.value = false
    return
  }
  loading.value = true
  debounceTimer = setTimeout(() => runSearch(q.trim()), 250)
})

async function runSearch(q) {
  // Escape % and _ so they aren't treated as wildcards if user types them
  const pattern = `%${q.replace(/[%_]/g, '\\$&')}%`

  try {
    const [playerRes, teamRes] = await Promise.all([
      supabase
        .from('players')
        .select(`
          id, name, position, is_pitcher,
          team:teams ( id, abbrev, name )
        `)
        .ilike('name', pattern)
        .order('name', { ascending: true })
        .limit(10),
      supabase
        .from('teams')
        .select('id, abbrev, name')
        .or(`abbrev.ilike.${pattern},name.ilike.${pattern}`)
        .order('abbrev', { ascending: true })
        .limit(8),
    ])

    players.value = playerRes.data || []
    teams.value = teamRes.data || []
    highlightIndex.value = 0
  } catch (e) {
    console.error('[SearchModal] query failed:', e)
    players.value = []
    teams.value = []
  } finally {
    loading.value = false
  }
}

// ── Flat result list for keyboard nav ──────────────────────────
// Players first, then teams. Each entry carries enough info to
// dispatch on Enter.
const flatResults = computed(() => {
  const out = []
  for (const p of players.value) {
    out.push({ kind: 'player', id: p.id, payload: p })
  }
  for (const t of teams.value) {
    out.push({ kind: 'team', id: t.id, payload: t })
  }
  return out
})

watch(flatResults, () => {
  // Clamp highlight if results shrink
  if (highlightIndex.value >= flatResults.value.length) {
    highlightIndex.value = Math.max(0, flatResults.value.length - 1)
  }
})

function moveHighlight(delta) {
  const n = flatResults.value.length
  if (!n) return
  highlightIndex.value = (highlightIndex.value + delta + n) % n
}

function selectByIndex(i) {
  const item = flatResults.value[i]
  if (!item) return
  selectResult(item)
}

function selectResult(item) {
  if (item.kind === 'player') {
    router.push({ name: 'player', params: { playerId: item.id } })
  } else if (item.kind === 'team') {
    // Route to team deep dive. payload may be a team record (live search)
    // or a favorited team entry — both have `abbrev`.
    const abbrev = item.payload?.abbrev
    if (abbrev) {
      router.push({ name: 'team', params: { abbrev } })
    } else {
      router.push({ name: 'slate' })
    }
  }
  close()
}

// ── Input keyboard handling ────────────────────────────────────
function handleInputKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    moveHighlight(+1)
    return
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    moveHighlight(-1)
    return
  }
  if (e.key === 'Enter') {
    e.preventDefault()
    selectByIndex(highlightIndex.value)
  }
}

// ── Highlight helpers ──────────────────────────────────────────
// Each rendered row needs to know its index in the flat list so it
// can highlight when selected.
function playerFlatIndex(i) {
  return i
}
function teamFlatIndex(i) {
  return players.value.length + i
}
</script>

<template>
  <Teleport to="body">
    <transition name="search-fade">
      <div
        v-if="isOpen"
        class="search-overlay"
        @click.self="close"
      >
        <div class="search-modal">
          <!-- Input row -->
          <div class="search-input-row">
            <span class="label-bracket text-signal-400 shrink-0">find</span>
            <input
              ref="inputRef"
              v-model="query"
              type="text"
              placeholder="Player name or team abbrev (e.g. NYY)…"
              class="search-input"
              @keydown="handleInputKey"
              autocomplete="off"
              spellcheck="false"
            />
            <!-- Desktop: keyboard hint. Mobile: touch-friendly close button.
                 Both target the same close() action so users on every device
                 have a discoverable way to dismiss. -->
            <span class="search-hint">
              <kbd>esc</kbd> to close
            </span>
            <button
              type="button"
              @click="close"
              class="search-close-btn"
              aria-label="Close search"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- Results body -->
          <div class="search-body">
            <!-- Empty state: nothing typed yet -->
            <template v-if="!query.trim()">
              <!-- Favorites surfaced when present -->
              <div v-if="favCount > 0">
                <div v-if="favPlayers.length" class="search-section">
                  <div class="search-section-head">
                    <span class="label-bracket text-fg-500">★ favorites · players</span>
                    <span class="label-caps !text-[8px] opacity-70">{{ favPlayers.length }}</span>
                  </div>
                  <button
                    v-for="fp in favPlayers"
                    :key="`fav-p-${fp.id}`"
                    type="button"
                    @click="selectResult({ kind: 'player', id: fp.id, payload: fp })"
                    class="search-row"
                  >
                    <span class="search-row-name">{{ fp.name }}</span>
                    <span class="search-row-meta">
                      <span v-if="fp.team_abbrev" class="label-bracket text-signal-400">{{ fp.team_abbrev }}</span>
                      <span v-if="fp.position" class="label-caps !text-[8px]">{{ fp.position }}</span>
                      <FavoriteStar kind="player" size="sm" :item="fp" />
                    </span>
                  </button>
                </div>

                <div v-if="favTeams.length" class="search-section">
                  <div class="search-section-head">
                    <span class="label-bracket text-fg-500">★ favorites · teams</span>
                    <span class="label-caps !text-[8px] opacity-70">{{ favTeams.length }}</span>
                  </div>
                  <button
                    v-for="ft in favTeams"
                    :key="`fav-t-${ft.id}`"
                    type="button"
                    @click="selectResult({ kind: 'team', id: ft.id, payload: ft })"
                    class="search-row"
                  >
                    <span class="search-row-name">
                      <span class="label-bracket text-signal-400 mr-2">{{ ft.abbrev }}</span>
                      {{ ft.name }}
                    </span>
                    <span class="search-row-meta">
                      <FavoriteStar kind="team" size="sm" :item="ft" />
                    </span>
                  </button>
                </div>
              </div>

              <!-- No favorites yet -->
              <div v-else class="search-empty">
                <div class="search-empty-title">Search players or teams</div>
                <div class="search-empty-hint">
                  Try a partial name (<span class="display-num">jud</span>) or a team code (<span class="display-num">NYY</span>).
                  <br>
                  Use <kbd>↑</kbd> <kbd>↓</kbd> to walk, <kbd>Enter</kbd> to open.
                  <br><br>
                  Tap the ★ on any player or team to add them here.
                </div>
              </div>
            </template>

            <!-- Loading -->
            <div v-else-if="loading && !flatResults.length" class="search-empty">
              <div class="search-empty-hint">Searching…</div>
            </div>

            <!-- No results -->
            <div v-else-if="!flatResults.length" class="search-empty">
              <div class="search-empty-title">No matches</div>
              <div class="search-empty-hint">
                Nothing for "<span class="display-num">{{ query }}</span>" in players or teams.
              </div>
            </div>

            <!-- Results -->
            <template v-else>
              <!-- Players section -->
              <div v-if="players.length" class="search-section">
                <div class="search-section-head">
                  <span class="label-bracket text-fg-500">players</span>
                  <span class="label-caps !text-[8px] opacity-70">{{ players.length }}</span>
                </div>
                <button
                  v-for="(p, i) in players"
                  :key="`p-${p.id}`"
                  type="button"
                  @click="selectResult({ kind: 'player', id: p.id, payload: p })"
                  @mouseenter="highlightIndex = playerFlatIndex(i)"
                  class="search-row"
                  :class="{ 'is-active': highlightIndex === playerFlatIndex(i) }"
                >
                  <span class="search-row-name">{{ p.name }}</span>
                  <span class="search-row-meta">
                    <span v-if="p.team?.abbrev" class="label-bracket text-signal-400">{{ p.team.abbrev }}</span>
                    <span v-if="p.position" class="label-caps !text-[8px]">{{ p.position }}</span>
                    <FavoriteStar
                      kind="player"
                      size="sm"
                      :item="{ id: p.id, name: p.name, position: p.position, is_pitcher: p.is_pitcher, team: p.team }"
                    />
                  </span>
                </button>
              </div>

              <!-- Teams section -->
              <div v-if="teams.length" class="search-section">
                <div class="search-section-head">
                  <span class="label-bracket text-fg-500">teams</span>
                  <span class="label-caps !text-[8px] opacity-70">{{ teams.length }}</span>
                </div>
                <button
                  v-for="(t, i) in teams"
                  :key="`t-${t.id}`"
                  type="button"
                  @click="selectResult({ kind: 'team', id: t.id, payload: t })"
                  @mouseenter="highlightIndex = teamFlatIndex(i)"
                  class="search-row"
                  :class="{ 'is-active': highlightIndex === teamFlatIndex(i) }"
                >
                  <span class="search-row-name">
                    <span class="label-bracket text-signal-400 mr-2">{{ t.abbrev }}</span>
                    {{ t.name }}
                  </span>
                  <span class="search-row-meta">
                    <span class="label-caps !text-[8px] opacity-60">team</span>
                    <FavoriteStar kind="team" size="sm" :item="t" />
                  </span>
                </button>
              </div>
            </template>
          </div>

          <!-- Footer -->
          <div class="search-footer">
            <span class="label-caps !text-[8px] opacity-60">
              <kbd>ctrl</kbd>+<kbd>k</kbd> or <kbd>/</kbd> to open anywhere
            </span>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.search-overlay {
  position: fixed;
  inset: 0;
  background: rgba(8, 8, 10, 0.72);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  /* Top padding respects iPhone notch / dynamic island. Side padding
     respects landscape safe areas. */
  padding: calc(12vh + env(safe-area-inset-top))
           calc(16px + env(safe-area-inset-right))
           0
           calc(16px + env(safe-area-inset-left));
  z-index: 9000;
}

.search-modal {
  width: 100%;
  max-width: 560px;
  background: rgba(8, 8, 10, 0.97);
  border: 1px solid rgba(255, 42, 42, 0.30);
  box-shadow: 0 12px 60px rgba(0, 0, 0, 0.7),
              0 0 0 1px rgba(255, 42, 42, 0.06);
}

@media (max-width: 480px) {
  .search-overlay {
    padding: calc(6vh + env(safe-area-inset-top))
             calc(8px + env(safe-area-inset-right))
             0
             calc(8px + env(safe-area-inset-left));
  }
}

.search-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: rgba(255, 255, 255, 0.92);
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  /* 16px minimum prevents iOS Safari from auto-zooming the page when the
     input gets focus (small inputs trigger an annoying zoom-in). */
  font-size: 16px;
  min-width: 0;
}
.search-input::placeholder {
  color: rgba(255, 255, 255, 0.30);
}
.search-hint {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  color: rgba(255, 255, 255, 0.40);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  white-space: nowrap;
  flex-shrink: 0;
}

/* Touch-friendly close button. Hidden on desktop where the kbd hint
   and the click-outside-to-close handle dismissal. Shown on mobile
   where users have no Esc key. */
.search-close-btn {
  display: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  flex-shrink: 0;
  margin: -10px -8px -10px 0;  /* expand tap area without changing layout */
  border-radius: 4px;
  transition: color 100ms ease, background-color 100ms ease;
}
.search-close-btn:active {
  color: #fff;
  background: rgba(255, 42, 42, 0.10);
}

@media (max-width: 480px) {
  .search-hint { display: none; }
  .search-close-btn { display: inline-flex; }
}

.search-body {
  max-height: 60vh;
  overflow-y: auto;
}

.search-empty {
  padding: 28px 16px;
  text-align: center;
}
.search-empty-title {
  font-family: 'Syne', system-ui, sans-serif;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.55);
  margin-bottom: 6px;
}
.search-empty-hint {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.40);
  line-height: 1.6;
}

.search-section {
  padding: 6px 0;
}
.search-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 6px 14px 4px;
}

.search-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 8px 14px;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.75);
  text-align: left;
  cursor: pointer;
  transition: background-color 100ms ease;
}
.search-row:hover,
.search-row.is-active {
  background: rgba(255, 42, 42, 0.10);
  color: #fff;
}
.search-row.is-active {
  box-shadow: inset 2px 0 0 #FF2A2A;
}

.search-row-name {
  font-size: 13px;
  font-weight: 500;
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  flex: 1;
}
.search-row-meta {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
  flex-shrink: 0;
}

.search-footer {
  padding: 8px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  text-align: right;
}

kbd {
  display: inline-block;
  padding: 1px 5px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.04);
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  color: rgba(255, 255, 255, 0.65);
  line-height: 1;
  margin: 0 1px;
}

/* Transition */
.search-fade-enter-active,
.search-fade-leave-active {
  transition: opacity 140ms ease;
}
.search-fade-enter-active .search-modal,
.search-fade-leave-active .search-modal {
  transition: transform 140ms ease, opacity 140ms ease;
}
.search-fade-enter-from,
.search-fade-leave-to {
  opacity: 0;
}
.search-fade-enter-from .search-modal {
  opacity: 0;
  transform: translateY(-12px);
}
.search-fade-leave-to .search-modal {
  opacity: 0;
  transform: translateY(-8px);
}
</style>

<script setup>
// LogBetModal.vue
// Opens when user clicks "log bet" on a batter row.
// Pre-fills with projection context, user enters stake.

import { ref, computed, watch } from 'vue'
import { useBetLog } from '../composables/useBetLog'

const props = defineProps({
  // Required for log
  open: Boolean,
  gameId: { type: Number, default: null },
  player: { type: Object, default: () => ({}) },         // { id, name, mlbam_id }
  proj:   { type: Object, default: () => ({}) },         // projection row
  // Optional preset
  marketMode: { type: String, default: 'hr' },           // 'hr' | 'hits'
})
const emit = defineEmits(['close', 'logged'])

const { insertBet } = useBetLog()

// ─── Local form state ───
const market = ref('hr_anytime')
const side   = ref('yes')
const line   = ref(null)
const odds   = ref(null)        // american
const stake  = ref(10)
const notes  = ref('')
const saving = ref(false)
const errMsg = ref('')

// Reset & prefill whenever modal opens
watch(() => props.open, (isOpen) => {
  if (!isOpen) return
  errMsg.value = ''
  saving.value = false
  notes.value  = ''

  if (props.marketMode === 'hits') {
    market.value = 'hits_yes'
    side.value   = 'yes'
    line.value   = 0.5
  } else {
    market.value = 'hr_anytime'
    side.value   = 'yes'
    line.value   = 0.5
  }
  odds.value = props.proj?.best_american_odds ?? null
  // keep stake from last time as a small UX nicety
})

const projectedPct = computed(() => {
  const p = props.proj?.projected_prob
  return p == null ? null : (p * 100)
})

const edgePct = computed(() => {
  const e = props.proj?.edge
  return e == null ? null : (e * 100)
})

const potentialPayout = computed(() => {
  if (!odds.value || !stake.value) return null
  const o = parseFloat(odds.value)
  const s = parseFloat(stake.value)
  if (Number.isNaN(o) || Number.isNaN(s)) return null
  const profit = o >= 0 ? (s * o / 100) : (s * 100 / -o)
  return s + profit
})

const potentialProfit = computed(() => {
  if (potentialPayout.value == null || !stake.value) return null
  return potentialPayout.value - parseFloat(stake.value)
})

async function submit() {
  errMsg.value = ''
  if (!props.player?.id || !props.gameId) {
    errMsg.value = 'Missing player or game context.'
    return
  }
  const stakeNum = parseFloat(stake.value)
  const oddsNum  = parseInt(odds.value, 10)
  if (!stakeNum || stakeNum <= 0) { errMsg.value = 'Stake must be > 0.'; return }
  if (Number.isNaN(oddsNum))      { errMsg.value = 'Odds required.'; return }

  saving.value = true
  try {
    await insertBet({
      game_id:           props.gameId,
      player_id:         props.player.id,
      market:            market.value,
      side:              side.value,
      line:              line.value,
      american_odds:     oddsNum,
      stake:             stakeNum,
      edge_at_placement: props.proj?.edge ?? null,
      projected_prob:    props.proj?.projected_prob ?? null,
      model_version:     props.proj?.model_version ?? null,
      notes:             notes.value || null,
    })
    emit('logged')
    emit('close')
  } catch (e) {
    console.error(e)
    errMsg.value = e.message || 'Failed to save bet.'
  } finally {
    saving.value = false
  }
}

function close() {
  if (!saving.value) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center" @click.self="close">
        <!-- backdrop -->
        <div class="absolute inset-0 bg-black/70 backdrop-blur-sm"></div>

        <!-- panel -->
        <div class="relative w-[min(440px,92vw)] bg-bg-50 border border-bg-200/60 rounded-sm shadow-2xl">
          <!-- reticle corners -->
          <div class="absolute top-0 left-0 w-3 h-3 border-t border-l border-accent-red/60"></div>
          <div class="absolute top-0 right-0 w-3 h-3 border-t border-r border-accent-red/60"></div>
          <div class="absolute bottom-0 left-0 w-3 h-3 border-b border-l border-accent-red/60"></div>
          <div class="absolute bottom-0 right-0 w-3 h-3 border-b border-r border-accent-red/60"></div>

          <div class="p-5">
            <div class="flex items-center justify-between mb-4">
              <div>
                <div class="label-bracket text-accent-red mb-1">[ LOG BET ]</div>
                <div class="font-display text-lg text-fg-50 leading-tight">
                  {{ player.name || 'Player' }}
                </div>
              </div>
              <button
                class="text-fg-400 hover:text-fg-50 text-lg leading-none"
                @click="close"
                :disabled="saving"
              >×</button>
            </div>

            <!-- Projection context (read-only chips) -->
            <div class="grid grid-cols-3 gap-2 mb-4 text-[10px]">
              <div class="bg-bg-100/40 border border-bg-200/40 px-2 py-1.5">
                <div class="label-bracket !text-[8px] opacity-60 mb-0.5">PROJ</div>
                <div class="display-num text-fg-50">
                  {{ projectedPct == null ? '—' : projectedPct.toFixed(1) + '%' }}
                </div>
              </div>
              <div class="bg-bg-100/40 border border-bg-200/40 px-2 py-1.5">
                <div class="label-bracket !text-[8px] opacity-60 mb-0.5">EDGE</div>
                <div class="display-num"
                     :class="edgePct == null ? 'text-fg-400' : (edgePct >= 0 ? 'text-emerald-400' : 'text-accent-red')">
                  {{ edgePct == null ? '—' : (edgePct >= 0 ? '+' : '') + edgePct.toFixed(2) + '%' }}
                </div>
              </div>
              <div class="bg-bg-100/40 border border-bg-200/40 px-2 py-1.5">
                <div class="label-bracket !text-[8px] opacity-60 mb-0.5">MODEL</div>
                <div class="display-num text-fg-300 text-[10px]">
                  {{ proj.model_version || '—' }}
                </div>
              </div>
            </div>

            <!-- Form -->
            <div class="space-y-3">
              <!-- Market + Side -->
              <div class="grid grid-cols-2 gap-2">
                <div>
                  <div class="label-bracket !text-[8px] opacity-60 mb-1">[ MARKET ]</div>
                  <select v-model="market" class="bet-input">
                    <option value="hr_anytime">HR Anytime</option>
                    <option value="hits_yes">1+ Hits</option>
                  </select>
                </div>
                <div>
                  <div class="label-bracket !text-[8px] opacity-60 mb-1">[ SIDE ]</div>
                  <select v-model="side" class="bet-input">
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                  </select>
                </div>
              </div>

              <!-- Line + Odds -->
              <div class="grid grid-cols-2 gap-2">
                <div>
                  <div class="label-bracket !text-[8px] opacity-60 mb-1">[ LINE ]</div>
                  <input v-model.number="line" type="number" step="0.5" class="bet-input display-num" />
                </div>
                <div>
                  <div class="label-bracket !text-[8px] opacity-60 mb-1">[ ODDS (AMER.) ]</div>
                  <input v-model.number="odds" type="number" step="1" class="bet-input display-num" placeholder="+150 or -110" />
                </div>
              </div>

              <!-- Stake -->
              <div>
                <div class="label-bracket !text-[8px] opacity-60 mb-1">[ STAKE ($) ]</div>
                <input v-model.number="stake" type="number" step="1" min="0" class="bet-input display-num text-base" />
              </div>

              <!-- Payout preview -->
              <div v-if="potentialPayout != null" class="bg-bg-100/30 border border-bg-200/30 px-3 py-2 flex justify-between items-baseline">
                <div class="label-bracket !text-[8px] opacity-60">[ TO WIN ]</div>
                <div class="display-num text-fg-50">
                  ${{ potentialProfit.toFixed(2) }}
                  <span class="text-fg-400 text-xs ml-1">→ ${{ potentialPayout.toFixed(2) }}</span>
                </div>
              </div>

              <!-- Notes -->
              <div>
                <div class="label-bracket !text-[8px] opacity-60 mb-1">[ NOTES (optional) ]</div>
                <input v-model="notes" type="text" class="bet-input" placeholder="reasoning, parlay info, etc." />
              </div>

              <div v-if="errMsg" class="text-accent-red text-xs">{{ errMsg }}</div>

              <!-- Actions -->
              <div class="flex gap-2 pt-1">
                <button
                  @click="close"
                  :disabled="saving"
                  class="flex-1 py-2 text-xs label-bracket border border-bg-200/60 hover:border-bg-300 text-fg-300 hover:text-fg-50 transition"
                >[ CANCEL ]</button>
                <button
                  @click="submit"
                  :disabled="saving"
                  class="log-submit-btn flex-[1.4] py-2 text-xs"
                >{{ saving ? '[ SAVING… ]' : '[ LOG BET ]' }}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.bet-input {
  width: 100%;
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.12);
  color: rgb(245,245,245);
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 13px;
  padding: 6px 8px;
  border-radius: 2px;
  outline: none;
  transition: border-color 0.15s;
}
.bet-input:focus {
  border-color: var(--color-accent-red, #FF2A2A);
}
.bet-input.display-num {
  font-family: 'JetBrains Mono', monospace;
}

/* Explicit submit button — bypasses Tailwind color resolution issues */
.log-submit-btn {
  font-family: 'IBM Plex Sans', sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: #FF2A2A;
  color: #ffffff;
  border: 1px solid #FF2A2A;
  font-weight: 600;
  transition: all 0.15s;
  cursor: pointer;
}
.log-submit-btn:hover:not(:disabled) {
  background: #FF4444;
  border-color: #FF4444;
}
.log-submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>

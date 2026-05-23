<script setup>
/**
 * FavoriteStar.vue — toggle star for adding/removing favorites.
 *
 * Props:
 *   kind:   'player' | 'team'
 *   item:   the object to favorite (player or team record)
 *   size:   'sm' | 'md' | 'lg'  (visual size; sm = inline-table, md = card, lg = header)
 *
 * The component handles its own state via useFavorites() — parents just
 * pass the item to favorite and the star reacts. Clicking the star toggles.
 *
 * Click is stopped from propagating so this can sit inside a clickable
 * row/card without triggering the row's navigation handler.
 */

import { computed } from 'vue'
import { useFavorites } from '../composables/useFavorites.js'

const props = defineProps({
  kind: { type: String, required: true, validator: v => ['player', 'team'].includes(v) },
  item: { type: Object, required: true },
  size: { type: String, default: 'md' },
})

const { isPlayerFav, isTeamFav, togglePlayer, toggleTeam } = useFavorites()

const isFav = computed(() => {
  if (!props.item?.id) return false
  return props.kind === 'player'
    ? isPlayerFav(props.item.id)
    : isTeamFav(props.item.id)
})

function onClick(e) {
  e.preventDefault()
  e.stopPropagation()
  if (props.kind === 'player') togglePlayer(props.item)
  else toggleTeam(props.item)
}

const sizeClass = computed(() => `fav-star--${props.size}`)
</script>

<template>
  <button
    type="button"
    class="fav-star"
    :class="[sizeClass, { 'is-fav': isFav }]"
    :aria-label="isFav ? 'Remove from favorites' : 'Add to favorites'"
    :title="isFav ? 'Remove from favorites' : 'Add to favorites'"
    @click="onClick"
  >
    <!-- Filled star when favorited, outline when not -->
    <svg
      v-if="isFav"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 2.5l3.09 6.26 6.91 1.01-5 4.87 1.18 6.88L12 18.27l-6.18 3.25L7 14.64l-5-4.87 6.91-1.01L12 2.5z" />
    </svg>
    <svg
      v-else
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2.5l3.09 6.26 6.91 1.01-5 4.87 1.18 6.88L12 18.27l-6.18 3.25L7 14.64l-5-4.87 6.91-1.01L12 2.5z" />
    </svg>
  </button>
</template>

<style scoped>
.fav-star {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: transparent;
  border: none;
  cursor: pointer;
  color: rgba(255, 255, 255, 0.30);
  transition: color 140ms ease, transform 100ms ease;
  flex-shrink: 0;
  line-height: 0;
  position: relative;
}
/* Invisible tap-area expander. The visual star stays compact (12-22px)
   but the hit region extends to ~32-40px so thumbs can hit it without
   mis-tapping the row it's sitting on. The :before pseudo doesn't
   affect layout or interact with hover styles. */
.fav-star::before {
  content: '';
  position: absolute;
  inset: -10px;
}
.fav-star:hover {
  color: rgba(255, 42, 42, 0.85);
  transform: scale(1.1);
}
.fav-star.is-fav {
  color: #FFD23F;
  filter: drop-shadow(0 0 4px rgba(255, 210, 63, 0.5));
}
.fav-star.is-fav:hover {
  color: #FFE066;
}

.fav-star svg {
  display: block;
}
.fav-star--sm svg { width: 12px; height: 12px; }
.fav-star--md svg { width: 16px; height: 16px; }
.fav-star--lg svg { width: 22px; height: 22px; }
</style>

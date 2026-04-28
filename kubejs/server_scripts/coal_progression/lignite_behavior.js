// =============================================================================
// Coal differentiation: Lignite runtime server-side behavior
//
// Drop behavior is handled by .drops('logisticalpack:lignite') in the block
// registration (see startup_scripts/coal_progression/register_lignite.js).
// Burn time is also configured there because ItemEvents.modification is a
// startup-only event in KubeJS NeoForge 1.21.1.
//
// This file only handles XP drops on mining (vanilla coal_ore gives 0-2 XP,
// so lignite matches that). Silk-touched mining drops the block itself with
// no XP, mirroring vanilla.
//
// Trello: https://trello.com/c/RHyQXtlW
// =============================================================================

BlockEvents.broken(event => {
  const id = event.block.id
  if (id !== 'logisticalpack:lignite_ore' &&
      id !== 'logisticalpack:deepslate_lignite_ore') return

  if (!event.player || event.player.isCreative()) return

  const tool = event.player.mainHandItem
  const hasSilk = tool && tool.enchantments &&
                  tool.enchantments.some(e => String(e.id) === 'minecraft:silk_touch')
  if (hasSilk) return

  const xp = Math.floor(Math.random() * 3)
  if (xp > 0) event.block.popExperience(xp)
})

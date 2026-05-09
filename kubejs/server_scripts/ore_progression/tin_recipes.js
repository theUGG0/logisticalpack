// =============================================================================
// Tin smelting/blasting: raw_tin -> tin_ingot.
// Mirrors vanilla raw-iron / raw-copper smelt timings.
// =============================================================================

ServerEvents.recipes(event => {
  event.smelting('logisticalpack:tin_ingot', 'logisticalpack:raw_tin')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_smelting')

  event.blasting('logisticalpack:tin_ingot', 'logisticalpack:raw_tin')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_blasting')

  event.smelting('logisticalpack:tin_ingot', 'logisticalpack:tin_ore')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_ore_smelting')

  event.blasting('logisticalpack:tin_ingot', 'logisticalpack:tin_ore')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_ore_blasting')

  event.smelting('logisticalpack:tin_ingot', 'logisticalpack:deepslate_tin_ore')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_deepslate_ore_smelting')

  event.blasting('logisticalpack:tin_ingot', 'logisticalpack:deepslate_tin_ore')
    .xp(0.7)
    .id('logisticalpack:tin_ingot_from_deepslate_ore_blasting')
})

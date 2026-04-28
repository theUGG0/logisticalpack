// =============================================================================
// Coal differentiation: Lignite runtime behavior
//   - Sets lignite's furnace burn time to ~half of vanilla coal.
//   - Adds loot tables so mining the ore drops the lignite item (with
//     fortune support, mirroring vanilla coal_ore).
//   - DOES NOT touch the TFMG coking recipe — that recipe explicitly
//     consumes `minecraft:coal` (the item, not the #c:coals tag), so
//     lignite is naturally excluded from coke production. Verified
//     against the user-confirmed `tfmg:coking` recipe shape.
//
// Trello: https://trello.com/c/RHyQXtlW
// =============================================================================

// --- Burn time --------------------------------------------------------------
// Vanilla coal = 1600 ticks (= 8 items smelted).
// Real lignite has roughly half the energy density of bituminous coal, so
// 800 ticks (= 4 items smelted) reads as "lignite is bad fuel" without
// being unusable. Tune as desired.
ItemEvents.modification(event => {
  event.modify('logisticalpack:lignite', item => {
    item.burnTime = 800
  })
})

// --- Loot tables ------------------------------------------------------------
// Both ore variants drop 1 lignite item, with fortune bonuses matching
// vanilla coal_ore behavior (ore_drops type).
ServerEvents.blockLootTables(event => {
  event.addBlock('logisticalpack:lignite_ore', loot => {
    loot.addPool(pool => {
      pool.survivesExplosion()
      pool.addItem('logisticalpack:lignite')
        .applyOreBonus('minecraft:fortune')
        .explosionDecay()
    })
  })

  event.addBlock('logisticalpack:deepslate_lignite_ore', loot => {
    loot.addPool(pool => {
      pool.survivesExplosion()
      pool.addItem('logisticalpack:lignite')
        .applyOreBonus('minecraft:fortune')
        .explosionDecay()
    })
  })
})

// --- XP drops ---------------------------------------------------------------
// Vanilla coal_ore gives 0-2 XP on mining. Match that.
BlockEvents.broken(event => {
  if (event.block.id === 'logisticalpack:lignite_ore' ||
      event.block.id === 'logisticalpack:deepslate_lignite_ore') {
    if (event.player && !event.player.isCreative()) {
      const tool = event.player.mainHandItem
      const hasSilk = tool && tool.enchantments &&
                      tool.enchantments.some(e => String(e.id) === 'minecraft:silk_touch')
      if (!hasSilk) {
        const xp = Math.floor(Math.random() * 3)
        if (xp > 0) event.block.popExperience(xp)
      }
    }
  }
})

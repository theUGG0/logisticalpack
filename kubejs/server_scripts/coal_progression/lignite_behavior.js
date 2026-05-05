BlockEvents.broken(event => {
  const id = event.block.id
  if (id !== 'logisticalpack:lignite_ore' &&
      id !== 'logisticalpack:deepslate_lignite_ore') return

  if (!event.player || event.player.isCreative()) return

  const tool = event.player.mainHandItem
  const hasSilk = tool && tool.enchantments &&
                  tool.enchantments.some(e => String(e.id) === 'minecraft:silk_touch')
  if (hasSilk) return
})

LootJS.modifiers(event => {
    event
        .addBlockModifier("logisticalpack:lignite_ore")
        .dropExperience({ n: 5, p: 0.3 })
        .matchMainHand(ItemFilter.not(ItemFilter.hasEnchantment("minecraft:silk_touch")))
;
});
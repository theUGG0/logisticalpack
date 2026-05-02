BlockEvents.broken(event => {
  const id = event.block.id
  if (id !== 'logisticalpack:tin_ore' &&
      id !== 'logisticalpack:deepslate_tin_ore') return

  if (!event.player || event.player.isCreative()) return

  const tool = event.player.mainHandItem
  const hasSilk = tool && tool.enchantments &&
                  tool.enchantments.some(e => String(e.id) === 'minecraft:silk_touch')
  if (hasSilk) return

  const xp = Math.floor(Math.random() * 3)
  if (xp > 0) event.block.popExperience(xp)
})

StartupEvents.registry('block', event => {
  event.create('logisticalpack:primordial_blaze_vent')
    .displayName('Primordial Blaze Vent')
    .hardness(50.0)
    .resistance(3.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_diamond_tool')
    .randomTick(ctx => {
      const above = ctx.level.getBlockState(ctx.pos.above())
      const id = above.getBlock().arch$registryName().toString()
      if (above.isAir || id === 'minecraft:water') {
        ctx.level.setBlock(ctx.pos.above(), Block.getBlock('kubejs:primordial_blaze').defaultBlockState(), 3)
      }
    })
    .item(item => { item
    })
})
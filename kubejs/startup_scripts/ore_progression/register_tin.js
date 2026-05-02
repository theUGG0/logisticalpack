// =============================================================================
// Tin registration
//   - Tin is a starter ore (spawn-tier). Real tin forms in granite belts —
//     cool/temperate cratonic regions (Cornwall, Bolivia altiplano), so
//     placement is in the temperate spawn band rather than tropical or hot.
//   - Drops raw_tin which smelts to tin_ingot.
//   - Item tags use the standard #c:raw_materials/tin and #c:ingots/tin so
//     other mods (TFMG, Create) can interop if they pick up tin recipes later.
// =============================================================================

StartupEvents.registry('block', event => {
  event.create('logisticalpack:tin_ore')
    .displayName('Tin Ore')
    .hardness(3.0)
    .resistance(3.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_stone_tool')
    .tagBlock('c:ores')
    .tagBlock('c:ores/tin')
    .tagBlock('forge:ores/tin')
    .item(item => {
      item.tag('c:ores')
          .tag('c:ores/tin')
          .tag('forge:ores/tin')
    })

  event.create('logisticalpack:deepslate_tin_ore')
    .displayName('Deepslate Tin Ore')
    .hardness(4.5)
    .resistance(3.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_stone_tool')
    .tagBlock('c:ores')
    .tagBlock('c:ores/tin')
    .tagBlock('forge:ores/tin')
    .item(item => {
      item.tag('c:ores')
          .tag('c:ores/tin')
          .tag('forge:ores/tin')
    })
})

StartupEvents.registry('item', event => {
  event.create('logisticalpack:raw_tin')
    .displayName('Raw Tin')
    .tag('c:raw_materials')
    .tag('c:raw_materials/tin')
    .tag('forge:raw_materials/tin')

  event.create('logisticalpack:tin_ingot')
    .displayName('Tin Ingot')
    .tag('c:ingots')
    .tag('c:ingots/tin')
    .tag('forge:ingots/tin')
})

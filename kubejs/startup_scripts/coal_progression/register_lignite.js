// =============================================================================
// Coal differentiation: Lignite registration
//   - Adds lignite (low-grade brown coal) as a distinct ore + item.
//   - Lignite cannot be turned into coke (TFMG's coking recipe uses
//     `minecraft:coal` directly, so lignite is excluded by item identity).
//   - Tagged as #c:coals so generic "any coal" recipes still accept it,
//     but it's a *poor* coal: short burn time (set in server_scripts).
//
// Trello: https://trello.com/c/RHyQXtlW
// Modpack: https://trello.com/b/WlK2BBhg
//
// Textures: Drop the PNGs into:
//   kubejs/assets/logisticalpack/textures/block/lignite_ore.png
//   kubejs/assets/logisticalpack/textures/block/deepslate_lignite_ore.png
//   kubejs/assets/logisticalpack/textures/item/lignite.png
// Until then, KubeJS will use the missing-texture (purple/black) fallback.
// =============================================================================

StartupEvents.registry('block', event => {
  // Surface-tier lignite ore (stone-hosted). Real lignite is sedimentary and
  // shallow, so this is the variant that should appear most.
  event.create('logisticalpack:lignite_ore')
    .displayName('Lignite Ore')
    .hardness(3.0)              // same as coal_ore
    .resistance(3.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_stone_tool') // wood pick can't break it; stone+ can
    .tagBlock('c:ores')
    .tagBlock('c:ores/coal')                // common coal-ore tag
    .tagBlock('forge:ores/coal')            // legacy compat (some mods still read this)
    .item(item => {
      item.tag('c:ores')
          .tag('c:ores/coal')
          .tag('forge:ores/coal')
    })

  // Deepslate variant — present if a vein dips below 0, but lignite is
  // primarily shallow so this should be much rarer in worldgen.
  event.create('logisticalpack:deepslate_lignite_ore')
    .displayName('Deepslate Lignite Ore')
    .hardness(4.5)              // same as deepslate_coal_ore
    .resistance(3.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_stone_tool')
    .tagBlock('c:ores')
    .tagBlock('c:ores/coal')
    .tagBlock('forge:ores/coal')
    .item(item => {
      item.tag('c:ores')
          .tag('c:ores/coal')
          .tag('forge:ores/coal')
    })
})

StartupEvents.registry('item', event => {
  // The lignite "lump" — drops directly when mining the ore (no fortune
  // bonus for now; can be tuned later with a loot table override).
  event.create('logisticalpack:lignite')
    .displayName('Lignite')
    .tag('c:coals')             // KEY: lignite IS a coal, just a bad one.
    .tag('forge:coals')          // legacy compat
    .tooltip('A low-grade brown coal. Burns poorly. Cannot be coked.')
})

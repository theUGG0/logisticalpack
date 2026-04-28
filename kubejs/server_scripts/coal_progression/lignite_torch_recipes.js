// =============================================================================
// Lignite torch + soul torch recipes
//   Vanilla's torch and soul torch recipes use [coal, charcoal] as the
//   fuel ingredient (NOT the #minecraft:coals tag, just an explicit
//   alternatives list). Lignite isn't in that list, so we add parallel
//   recipes with lignite as the fuel. Output rates match vanilla exactly.
//
//   Vanilla:
//     torch:      X/# (X=coal|charcoal, #=stick)        -> 4 torches
//     soul torch: X/#/S (S=#soul_fire_base_blocks)      -> 4 soul torches
//
// Trello: https://trello.com/c/RHyQXtlW
// =============================================================================

ServerEvents.recipes(event => {
  // Torch from lignite: X / # -> 4 torches. Identical layout to vanilla.
  event.shaped(
    Item.of('minecraft:torch', 4),
    [
      'X',
      '#'
    ],
    {
      X: 'logisticalpack:lignite',
      '#': 'minecraft:stick'
    }
  ).id('logisticalpack:torch_from_lignite')

  // Soul torch from lignite: X / # / S -> 4 soul torches.
  // S uses the vanilla #minecraft:soul_fire_base_blocks tag so both
  // soul sand and soul soil work, matching vanilla.
  event.shaped(
    Item.of('minecraft:soul_torch', 4),
    [
      'X',
      '#',
      'S'
    ],
    {
      X: 'logisticalpack:lignite',
      '#': 'minecraft:stick',
      S: '#minecraft:soul_fire_base_blocks'
    }
  ).id('logisticalpack:soul_torch_from_lignite')
})

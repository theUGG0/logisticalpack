// Copper-tier empty blaze burner. Vanilla recipe is 4 iron plates +
// 1 netherrack. With nether locked to steel in this pack, players need
// an alternative path. Blazes spawn in volcanic / magma cave biomes,
// so blaze rods are reachable; the burner shell was the missing piece.
//
// 4 copper sheets + 3 copper blocks (= 31 copper ingots) + 1 magma
// block, with hammer durability on top. The open top makes it look
// like the brazier it is.

ServerEvents.recipes(event => {
  event.shaped(
    'create:empty_blaze_burner',
    [
      'I I',
      'IAI',
      'BBB'
    ],
    {
      I: 'create:copper_sheet',
      A: 'minecraft:magma_block',
      B: 'minecraft:copper_block'
    }
  ).id('logisticalpack:empty_blaze_burner_from_copper')
})

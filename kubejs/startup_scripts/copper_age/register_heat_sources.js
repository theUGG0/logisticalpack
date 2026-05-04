// Register coal-fueled vanilla campfires as a DINGUS heat source.
// We hijack the signal_fire property to mark coal-fueled state - normally
// only true when a hay bale sits below, which players rarely do in
// Create setups. signal_fire=true also gives a taller smoke column,
// providing visual feedback. CreateHeatJS BlockStateHeatSource matches
// states exactly, so all four facing variants are registered.

CreateHeatJS.registerHeatEvent(event => {
  event.registerHeat("DINGUS", builder => builder
    .color(0xFF8C00)
    .addHeatSource("minecraft:campfire[lit=true,signal_fire=true,facing=north,waterlogged=false]")
    .addHeatSource("minecraft:campfire[lit=true,signal_fire=true,facing=south,waterlogged=false]")
    .addHeatSource("minecraft:campfire[lit=true,signal_fire=true,facing=east,waterlogged=false]")
    .addHeatSource("minecraft:campfire[lit=true,signal_fire=true,facing=west,waterlogged=false]")
    .satisfies("HEATED"))
})

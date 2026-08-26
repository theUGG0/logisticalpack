// Marker block placed at the center of every trade-town structure.
// KubeJS chunk-scan watches for this block to trigger town registration.
// High hardness/resistance to discourage griefing if a player ever finds one.

StartupEvents.registry('block', event => {
  event.create('logisticalpack:town_center')
    .displayName('Town Center')
    .hardness(50.0)
    .resistance(1200.0)
    .requiresTool(true)
    .tagBlock('minecraft:mineable/pickaxe')
    .tagBlock('minecraft:needs_diamond_tool')
    .tagBlock('logisticalpack:town_markers')
})

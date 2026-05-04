// =============================================================================
// Remove Create recipes that allow fully independent ore generation.
//   Vanilla Create lets players bypass ore mining entirely via:
//     - Washing gravel    -> iron nugget (+ flint, clay)
//     - Crushing gravel   -> flint + chance iron nugget
//     - Washing soul sand -> gold nugget (+ quartz)
//     - Washing red sand  -> gold nugget (+ regular sand)
//   These inputs are all renewable (gravel/sand from cobble generators
//   via Create; soul sand farmable in the Nether). Strip those routes.
//
//   These filters target input+output together, so legitimate ore
//   doubling (e.g. crushing iron ore -> crushed raw iron + chance
//   iron nugget) is preserved.
// =============================================================================

ServerEvents.recipes(event => {
  event.remove({ mod: 'create', input: 'minecraft:gravel',    output: 'minecraft:iron_nugget' })
  event.remove({ mod: 'create', input: 'minecraft:soul_sand', output: 'minecraft:gold_nugget' })
  event.remove({ mod: 'create', input: 'minecraft:red_sand',  output: 'minecraft:gold_nugget' })
})

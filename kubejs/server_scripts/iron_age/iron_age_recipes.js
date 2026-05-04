// Iron-age 6x ore-doubling step. For every item in #create:crushed_raw_materials,
// add a HEATED mixing recipe that out-yields washing by ~1.5x. Combined
// with the existing crushing+washing chain (4x), this brings refined
// metals to 6x effective. Heat must come from a coal-fueled hot campfire
// (or a blaze burner, post-steel).
//
// Output is the corresponding ingot via #c:ingots/<metal> lookup, with
// a chance bonus that averages the 0.5-ingot uplift over washing.

ServerEvents.recipes(event => {
  const stacks = Ingredient.of('#create:crushed_raw_materials').stacks
  stacks.forEach(stack => {
    const crushedId = String(stack.id)
    const m = crushedId.match(/^([a-z0-9_]+):crushed_raw_([a-z0-9_]+)$/)
    if (!m) return
    const metal = m[2]

    const candidates = [m[1] + ':' + metal + '_ingot', 'minecraft:' + metal + '_ingot']
    let ingot = null
    for (const c of candidates) {
      if (Item.exists(c)) { ingot = c; break }
    }
    if (!ingot) {
      console.warn('iron_age_recipes: no ingot found for ' + crushedId)
      return
    }
    console.info('iron_age_recipes: ' + crushedId + ' -> ' + ingot)

    event.recipes.create.mixing(
      [
        Item.of(ingot, 1),
        CreateItem.of(ingot, 0.5)
      ],
      [crushedId]
    ).heatLevel("DINGUS").id('logisticalpack:iron_age_refining_' + metal)
  })

  event.recipes.create.mixing("minecraft:diamond", "minecraft:coal_block").heatLevel("DINGUS");
})

ServerEvents.recipes(event => {
  const oreToIngot = {
    iron:   'minecraft:iron_ingot',
    copper: 'minecraft:copper_ingot',
    gold:   'minecraft:gold_ingot',
    tin:    'bronze:tin_ingot',
    zinc:   'create:zinc_ingot',
    lead:   'tfmg:lead_ingot',
    nickel: 'tfmg:nickel_ingot',
    lithium:'tfmg:lithium_ingot'
  }

  for (const [metal, ingot] of Object.entries(oreToIngot)) {
    event.smelting(ingot, 'logisticalpack:crude_crushed_' + metal)
      .xp(0.7)
      .id('logisticalpack:smelting_crude_crushed_' + metal)
  }
})

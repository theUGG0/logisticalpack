ServerEvents.recipes(event => {
  const rawOreToMetal = {
    iron:   'minecraft:raw_iron',
    copper: 'minecraft:raw_copper',
    gold:   'minecraft:raw_gold',
    tin:    'bronze:raw_tin',
    zinc:   'create:raw_zinc',
    lead:   'tfmg:raw_lead',
    nickel: 'tfmg:raw_nickel',
    lithium:'tfmg:raw_lithium'
  }

  for (const [metal, rawOre] of Object.entries(rawOreToMetal)) {
    event.recipes.create.milling(
      Item.of('logisticalpack:crude_crushed_' + metal, 2),
      rawOre,
    ).id('logisticalpack:milling_crude_crushed_' + metal)
  }
})

ServerEvents.recipes(event => {
  const metalToCrushed = {
    iron:   'create:crushed_raw_iron',
    copper: 'create:crushed_raw_copper',
    gold:   'create:crushed_raw_gold',
    tin:    'logisticalpack:crushed_raw_tin',
    zinc:   'create:crushed_raw_zinc',
    lead:   'create:crushed_raw_lead',
    nickel: 'create:crushed_raw_nickel',
    lithium:'tfmg:crushed_raw_lithium'
  }

  for (const [metal, crushed] of Object.entries(metalToCrushed)) {
    event.recipes.create.splashing(
      [Item.of(crushed, 1), CreateItem.of(crushed, 0.5)],
      'logisticalpack:crude_crushed_' + metal
    ).id('logisticalpack:washing_crude_crushed_' + metal)
  }
})

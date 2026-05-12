ServerEvents.recipes(event => {
  const metals = {
    iron:   { crushed: 'create:crushed_raw_iron',   raw: 'minecraft:raw_iron',   ore: 'minecraft:iron_ore',   deepslate: 'minecraft:deepslate_iron_ore' },
    copper: { crushed: 'create:crushed_raw_copper',  raw: 'minecraft:raw_copper', ore: 'minecraft:copper_ore', deepslate: 'minecraft:deepslate_copper_ore' },
    gold:   { crushed: 'create:crushed_raw_gold',    raw: 'minecraft:raw_gold',   ore: 'minecraft:gold_ore',   deepslate: 'minecraft:deepslate_gold_ore' },
    tin:    { crushed: 'logisticalpack:crushed_raw_tin', raw: 'bronze:raw_tin',    ore: 'bronze:tin_ore',       deepslate: 'bronze:deepslate_tin_ore' },
    zinc:   { crushed: 'create:crushed_raw_zinc',    raw: 'create:raw_zinc',      ore: 'create:zinc_ore',      deepslate: 'create:deepslate_zinc_ore' },
    lead:   { crushed: 'create:crushed_raw_lead',    raw: 'tfmg:raw_lead',        ore: 'tfmg:lead_ore',        deepslate: 'tfmg:deepslate_lead_ore' },
    nickel: { crushed: 'create:crushed_raw_nickel',  raw: 'tfmg:raw_nickel',      ore: 'tfmg:nickel_ore',      deepslate: 'tfmg:deepslate_nickel_ore' },
    lithium:{ crushed: 'tfmg:crushed_raw_lithium',   raw: 'tfmg:raw_lithium',     ore: 'tfmg:lithium_ore',     deepslate: 'tfmg:deepslate_lithium_ore' }
  }

  for (const [metal, ids] of Object.entries(metals)) {
    event.remove({ type: 'create:crushing', output: ids.crushed, input: ids.raw })
    event.remove({ type: 'create:crushing', output: ids.crushed, input: ids.ore })
    event.remove({ type: 'create:crushing', output: ids.crushed, input: ids.deepslate })

    let crude = 'logisticalpack:crude_crushed_' + metal

    event.recipes.create.crushing(
      [Item.of(crude, 1), CreateItem.of(crude, 0.5)],
      ids.raw
    ).id('logisticalpack:crushing_raw_' + metal)

    event.recipes.create.crushing(
      [Item.of(crude, 1), CreateItem.of(crude, 0.5)],
      ids.ore
    ).id('logisticalpack:crushing_ore_' + metal)

    event.recipes.create.crushing(
      [Item.of(crude, 1), CreateItem.of(crude, 0.5)],
      ids.deepslate
    ).id('logisticalpack:crushing_deepslate_ore_' + metal)
  }
})

StartupEvents.registry('item', event => {
  const metals = ['iron', 'copper', 'gold', 'lead', 'nickel', 'zinc', 'lithium']

  metals.forEach(metal => {
    event.create('logisticalpack:crude_crushed_' + metal)
      .displayName('Crude Crushed ' + metal.charAt(0).toUpperCase() + metal.slice(1))
  })

  event.create('logisticalpack:crushed_raw_tin')
    .displayName('Crushed Raw Tin')
})
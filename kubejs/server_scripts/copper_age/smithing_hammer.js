ServerEvents.recipes(event => {
  event.shaped(
    'logisticalpack:smithing_hammer',
    [
      'RS',
      '#R'
    ],
    {
      R: 'minecraft:smooth_stone',
	  S: 'minecraft:string',
      '#': 'minecraft:stick'
    }
  ).id('logisticalpack:smithing_hammer')

  event.shaped(
    'create:copper_sheet',
    [
      'H',
      'I'
    ],
    {
      H: 'logisticalpack:smithing_hammer',
      I: 'minecraft:copper_ingot'
    }
  )
  .damageIngredient('logisticalpack:smithing_hammer')
  .id('logisticalpack:copper_sheet_hammered')
})

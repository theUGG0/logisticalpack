ServerEvents.recipes(event => {
  event.shaped(
    'minecraft:saddle',
    [
      'LLL',
      'ISI'
    ],
    {
      L: 'minecraft:leather',
      I: 'minecraft:copper_ingot',
      S: 'minecraft:string'
    }
  ).id('logisticalpack:saddle_from_copper')
})

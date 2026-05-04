// Copper-tier alternatives for cart hardware (rope coupling, rope
// connector, wheel mount) plus the spring they depend on. Iron versions
// are left intact.

ServerEvents.recipes(event => {
  // Half output of vanilla iron spring (copper is a poorer spring metal).
  event.shaped(
    'simulated:spring',
    [
      'S',
      'N',
      'S'
    ],
    {
      S: 'create:copper_sheet',
      N: '#c:nuggets/copper'
    }
  ).id('logisticalpack:spring_from_copper')

  event.shaped(
    'simulated:rope_coupling',
    [
      ' S ',
      'NSN',
      ' S '
    ],
    {
      N: '#c:nuggets/copper',
      S: '#c:strings'
    }
  ).id('logisticalpack:rope_coupling_from_copper')

  // Yields 2: a copper block is 9 ingots, so the per-connector cost stays sane.
  event.shaped(
    Item.of('simulated:rope_connector', 2),
    [
      'P',
      'B',
      'P'
    ],
    {
      P: 'create:copper_sheet',
      B: 'minecraft:copper_block'
    }
  ).id('logisticalpack:rope_connector_from_copper')

  event.shaped(
    'offroad:wheel_mount',
    [
      'CP',
      'SP'
    ],
    {
      C: 'create:andesite_casing',
      S: 'simulated:spring',
      P: 'create:copper_sheet'
    }
  ).id('logisticalpack:wheel_mount_from_copper')
})

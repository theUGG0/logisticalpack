// Copper bridge to andesite alloy so Create's chain is reachable pre-iron.
// Vanilla iron and zinc recipes are left intact.

ServerEvents.recipes(event => {
  event.shaped(
    'create:andesite_alloy',
    [
      'ANA',
      'NAN',
      'ANA'
    ],
    {
      A: 'minecraft:andesite',
      N: '#c:nuggets/copper'
    }
  ).id('logisticalpack:andesite_alloy_from_copper')
})

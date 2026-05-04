// Stone-tier hand tool for cold-hammering copper into sheets pre-press.

StartupEvents.registry('item', event => {
  event.create('logisticalpack:smithing_hammer')
    .displayName('Smithing Hammer')
    .maxDamage(8)
    .maxStackSize(1)
    .tooltip('Cold-hammers copper ingots into sheets.')
})

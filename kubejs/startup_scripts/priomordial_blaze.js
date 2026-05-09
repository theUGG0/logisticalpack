// priority: 100
const $SoundActions = Java.loadClass('net.neoforged.neoforge.common.SoundActions')
const $SoundEvents = Java.loadClass('net.minecraft.sounds.SoundEvents')
const $ParticleTypes = Java.loadClass('net.minecraft.core.particles.ParticleTypes')

StartupEvents.registry('fluid', event => {
  event.create('primordial_blaze', 'thick')
    .displayName('Primordial Blaze')
    .tint(0xffa10a)
    .slopeFindDistance(2)
    .type(type => type
      .renderType(0)
      .stillTexture('kubejs:block/thick_fluid_still')
      .flowingTexture('kubejs:block/thick_fluid_flow')
      .sound($SoundActions.BUCKET_FILL, $SoundEvents.BUCKET_FILL_LAVA)
      .sound($SoundActions.BUCKET_EMPTY, $SoundEvents.BUCKET_EMPTY_LAVA)
      .canSwim(false)
      .canDrown(false)
      .density(3000)
      .viscosity(6000)
    )
})
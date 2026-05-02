// =============================================================================
// /strip_to_ores [radius]
//   Admin/debug tool. Replaces every non-ore block with air in a square of
//   side (2*radius+1) chunks centered on the executing player, from world
//   minBuildHeight to maxBuildHeight. Keeps anything tagged #c:ores plus
//   bedrock.
//
//   Default radius = 5 (=> 11x11 = 121 chunks, ~12M block touches).
//   The op needs permission level >= 2.
//
//   WARNING: synchronous; expect a multi-second freeze at radius 5.
// =============================================================================

// All Java classes go in one object so we don't collide with KubeJS globals
// like `Blocks`, `Commands`, `Registries`, etc.
const MC = {
  Blocks:     Java.loadClass('net.minecraft.world.level.block.Blocks'),
  Commands:   Java.loadClass('net.minecraft.commands.Commands'),
  IntArg:     Java.loadClass('com.mojang.brigadier.arguments.IntegerArgumentType'),
  MutPos:     Java.loadClass('net.minecraft.core.BlockPos$MutableBlockPos'),
  TagKey:     Java.loadClass('net.minecraft.tags.TagKey'),
  Registries: Java.loadClass('net.minecraft.core.registries.Registries'),
  ResLoc:     Java.loadClass('net.minecraft.resources.ResourceLocation'),
}

const ORES_TAG = MC.TagKey.create(MC.Registries.BLOCK, MC.ResLoc.parse('c:ores'))

ServerEvents.commandRegistry(event => {
  event.register(
    MC.Commands.literal('strip_to_ores')
      .requires(src => src.hasPermission(2))
      .executes(ctx => strip(ctx.source, 5))
      .then(
        MC.Commands.argument('radius', MC.IntArg.integer(0, 16))
          .executes(ctx => strip(ctx.source, MC.IntArg.getInteger(ctx, 'radius')))
      )
  )
})

function strip(src, radius) {
  const player = src.player
  if (!player) {
    src.sendFailure(Text.of('Must be run by a player.'))
    return 0
  }
  const lvl = player.level
  const cx = player.blockX >> 4
  const cz = player.blockZ >> 4
  const minY = lvl.getMinBuildHeight()
  const maxY = lvl.getMaxBuildHeight()
  const air = MC.Blocks.AIR.defaultBlockState()
  const pos = new MC.MutPos()
  let cleared = 0
  let kept = 0

  const total = (2 * radius + 1) * (2 * radius + 1)
  src.sendSystemMessage(Text.of(`Stripping ${total} chunks (y=${minY}..${maxY - 1})...`))

  for (let dx = -radius; dx <= radius; dx++) {
    for (let dz = -radius; dz <= radius; dz++) {
      var baseX = (cx + dx) << 4
      var baseZ = (cz + dz) << 4
      for (let lx = 0; lx < 16; lx++) {
        for (let lz = 0; lz < 16; lz++) {
          for (let y = minY; y < maxY; y++) {
            pos.set(baseX + lx, y, baseZ + lz)
            var state = lvl.getBlockState(pos)
            if (state.isAir()) continue
            if (state.is(ORES_TAG)) { kept++; continue }
            if (state.block === MC.Blocks.BEDROCK) { kept++; continue }
            // setBlock flags: 2 = send to client, 16 = no neighbor updates
            lvl.setBlock(pos, air, 2 | 16)
            cleared++
          }
        }
      }
    }
  }
  src.sendSuccess(() => Text.of(`Stripped ${cleared} blocks; kept ${kept} ores/bedrock.`), false)
  return cleared
}

// =============================================================================
// Loot-table chest audit toolkit (1.21.1 NeoForge / KubeJS).
//
// Commands (all op-only, perm 2):
//   /audit_nearby_chests [radius]                -- peek-only scan
//   /audit_nearby_chests <radius> unpack         -- destructive: actually fills
//   /audit_chest_here                            -- inspect chest under player
//   /audit_chest_at <pos> [unpack]
//   /audit_chest_target [unpack]                 -- raycast: chest you're looking at
//   /audit_chest_dump <pos>                      -- raw BE NBT dump
//   /audit_nearby_chests_at <pos> <radius> [unpack]   (kept for back-compat)
//
// What "peek" means: we resolve the LootTable from the server's reloadable
// registries and call LootTable.getRandomItems(params, seed). This produces
// the items that *would* be generated WITHOUT clearing the chest's LootTable
// tag. Run it as many times as you like.
//
// What "unpack" means: we call RandomizableContainer.unpackLootTable(player),
// which in 1.21.1 (a) clears the chest's LootTable tag and (b) writes items
// into the BE.
//
// Critical fact about 1.21.1's RandomizableContainer.unpackLootTable (decoded
// from the actual class bytecode):
//
//   default void unpackLootTable(Player player) {
//       Level level = this.getLevel();
//       BlockPos pos = this.getBlockPos();
//       ResourceKey<LootTable> key = this.getLootTable();
//       if (key != null && level != null && level.getServer() != null) {
//           ...
//           this.setLootTable(null);
//           ...
//           lootTable.fill(this, params, this.getLootTableSeed());
//       }
//       // <-- silently does nothing if key is null. No log, no exception.
//   }
//
// In other words: a chest that has already been opened once (or had a hopper
// pull from it once) has key=null forever. A naive "unpack and read" sees
// "table=n/a, contents=whatever's still inside" and looks broken when really
// the loot table was consumed long ago.
//
// Discovery: we iterate `chunk.getBlockEntities()` for chunks in the radius.
// This avoids the block-state-scan path entirely (which, per a comment in the
// original script, can return stale results in tight loops under Rhino).
//
// Visual debugging: every found chest gets a particle marker.
//   - HAPPY_VILLAGER (green)  : has an unconsumed loot table
//   - END_ROD       (white)   : no loot table, but non-empty contents
//   - SMOKE         (gray)    : empty container
//   - ANGRY_VILLAGER(red)     : inspection threw an exception
//
// Errors are written to logs/kubejs/server.log via console.error and ALSO
// surfaced to chat so you don't have to alt-tab to see what's wrong.
// =============================================================================

const CHEST_AUDIT_MC = {
  BlockPos: Java.loadClass('net.minecraft.core.BlockPos'),
  BlockPosArg: Java.loadClass('net.minecraft.commands.arguments.coordinates.BlockPosArgument'),
  BuiltInRegistries: Java.loadClass('net.minecraft.core.registries.BuiltInRegistries'),
  Commands: Java.loadClass('net.minecraft.commands.Commands'),
  Container: Java.loadClass('net.minecraft.world.Container'),
  RandomizableContainer: Java.loadClass('net.minecraft.world.RandomizableContainer'),
  IntArg: Java.loadClass('com.mojang.brigadier.arguments.IntegerArgumentType'),
  ServerLevel: Java.loadClass('net.minecraft.server.level.ServerLevel'),
  Vec3: Java.loadClass('net.minecraft.world.phys.Vec3'),
  HitResult: Java.loadClass('net.minecraft.world.phys.HitResult'),
  HitResultType: Java.loadClass('net.minecraft.world.phys.HitResult$Type'),
  LootParamsBuilder: Java.loadClass('net.minecraft.world.level.storage.loot.LootParams$Builder'),
  LootContextParams: Java.loadClass('net.minecraft.world.level.storage.loot.parameters.LootContextParams'),
  LootContextParamSets: Java.loadClass('net.minecraft.world.level.storage.loot.parameters.LootContextParamSets'),
  ParticleTypes: Java.loadClass('net.minecraft.core.particles.ParticleTypes'),
  CompoundTag: Java.loadClass('net.minecraft.nbt.CompoundTag'),
  AreaEffectCloud: Java.loadClass('net.minecraft.world.entity.AreaEffectCloud'),
  EntityType: Java.loadClass('net.minecraft.world.entity.EntityType'),
  TickTask: Java.loadClass('net.minecraft.server.TickTask'),
  ChatFormatting: Java.loadClass('net.minecraft.ChatFormatting'),
  Component: Java.loadClass('net.minecraft.network.chat.Component'),
  ClickEvent: Java.loadClass('net.minecraft.network.chat.ClickEvent'),
  ClickAction: Java.loadClass('net.minecraft.network.chat.ClickEvent$Action'),
  HoverEvent: Java.loadClass('net.minecraft.network.chat.HoverEvent'),
  HoverAction: Java.loadClass('net.minecraft.network.chat.HoverEvent$Action'),
}

// Reflection handles for the private Display setters. Cached once at module
// load. Callers go through invoke() to bypass JS visibility.
const _DISP_REFL = (function () {
  let setBlockState = null
  let setGlowColor = null
  let setViewRange = null
  try {
    const bd = Java.loadClass('net.minecraft.world.entity.Display$BlockDisplay')
    const bs = Java.loadClass('net.minecraft.world.level.block.state.BlockState')
    setBlockState = bd.getDeclaredMethod('setBlockState', bs)
    setBlockState.setAccessible(true)
  } catch (e) { try { console.warn('[chest-audit] reflect BlockDisplay.setBlockState failed: ' + e) } catch (_) {} }
  try {
    const d = Java.loadClass('net.minecraft.world.entity.Display')
    const intType = Java.loadClass('java.lang.Integer').TYPE
    setGlowColor = d.getDeclaredMethod('setGlowColorOverride', intType)
    setGlowColor.setAccessible(true)
  } catch (e) { try { console.warn('[chest-audit] reflect Display.setGlowColorOverride failed: ' + e) } catch (_) {} }
  try {
    const d = Java.loadClass('net.minecraft.world.entity.Display')
    const floatType = Java.loadClass('java.lang.Float').TYPE
    setViewRange = d.getDeclaredMethod('setViewRange', floatType)
    setViewRange.setAccessible(true)
  } catch (e) { try { console.warn('[chest-audit] reflect Display.setViewRange failed: ' + e) } catch (_) {} }
  return { setBlockState: setBlockState, setGlowColor: setGlowColor, setViewRange: setViewRange }
})()

ServerEvents.commandRegistry(event => {
  // ---- /audit_nearby_chests [radius] [unpack] -----------------------------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_nearby_chests')
      .requires(src => src.hasPermission(2))
      .executes(ctx => auditNearbyChests(ctx.source, 16, false))
      .then(
        CHEST_AUDIT_MC.Commands.argument('radius', CHEST_AUDIT_MC.IntArg.integer(1, 128))
          .executes(ctx => auditNearbyChests(ctx.source, CHEST_AUDIT_MC.IntArg.getInteger(ctx, 'radius'), false))
          .then(
            CHEST_AUDIT_MC.Commands.literal('unpack')
              .executes(ctx => auditNearbyChests(ctx.source, CHEST_AUDIT_MC.IntArg.getInteger(ctx, 'radius'), true))
          )
      )
  )

  // ---- /audit_chest_here [unpack] -----------------------------------------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_chest_here')
      .requires(src => src.hasPermission(2))
      .executes(ctx => auditPlayerChest(ctx.source, false))
      .then(
        CHEST_AUDIT_MC.Commands.literal('unpack')
          .executes(ctx => auditPlayerChest(ctx.source, true))
      )
  )

  // ---- /audit_chest_at <pos> [unpack] -------------------------------------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_chest_at')
      .requires(src => src.hasPermission(2))
      .then(
        CHEST_AUDIT_MC.Commands.argument('pos', CHEST_AUDIT_MC.BlockPosArg.blockPos())
          .executes(ctx => {
            const pos = CHEST_AUDIT_MC.BlockPosArg.getLoadedBlockPos(ctx, 'pos')
            return auditChestAt(ctx.source, pos.getX(), pos.getY(), pos.getZ(), false)
          })
          .then(
            CHEST_AUDIT_MC.Commands.literal('unpack')
              .executes(ctx => {
                const pos = CHEST_AUDIT_MC.BlockPosArg.getLoadedBlockPos(ctx, 'pos')
                return auditChestAt(ctx.source, pos.getX(), pos.getY(), pos.getZ(), true)
              })
          )
      )
  )

  // ---- /audit_chest_target [unpack] ---------------------------------------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_chest_target')
      .requires(src => src.hasPermission(2))
      .executes(ctx => auditChestTarget(ctx.source, false))
      .then(
        CHEST_AUDIT_MC.Commands.literal('unpack')
          .executes(ctx => auditChestTarget(ctx.source, true))
      )
  )

  // ---- /audit_chest_dump <pos> --------------------------------------------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_chest_dump')
      .requires(src => src.hasPermission(2))
      .then(
        CHEST_AUDIT_MC.Commands.argument('pos', CHEST_AUDIT_MC.BlockPosArg.blockPos())
          .executes(ctx => {
            const pos = CHEST_AUDIT_MC.BlockPosArg.getLoadedBlockPos(ctx, 'pos')
            return dumpChestNbtAt(ctx.source, pos.getX(), pos.getY(), pos.getZ())
          })
      )
  )

  // ---- /audit_nearby_chests_at <pos> <radius> [unpack] (back-compat) ------
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_nearby_chests_at')
      .requires(src => src.hasPermission(2))
      .then(
        CHEST_AUDIT_MC.Commands.argument('pos', CHEST_AUDIT_MC.BlockPosArg.blockPos())
          .then(
            CHEST_AUDIT_MC.Commands.argument('radius', CHEST_AUDIT_MC.IntArg.integer(1, 128))
              .executes(ctx => {
                const pos = CHEST_AUDIT_MC.BlockPosArg.getLoadedBlockPos(ctx, 'pos')
                const r = CHEST_AUDIT_MC.IntArg.getInteger(ctx, 'radius')
                return auditNearbyChestsAt(ctx.source, pos.getX(), pos.getY(), pos.getZ(), r, false)
              })
              .then(
                CHEST_AUDIT_MC.Commands.literal('unpack')
                  .executes(ctx => {
                    const pos = CHEST_AUDIT_MC.BlockPosArg.getLoadedBlockPos(ctx, 'pos')
                    const r = CHEST_AUDIT_MC.IntArg.getInteger(ctx, 'radius')
                    return auditNearbyChestsAt(ctx.source, pos.getX(), pos.getY(), pos.getZ(), r, true)
                  })
              )
          )
      )
  )

  // ---- /audit_loot_scan [radius] ------------------------------------------
  // Auto-unpack every loot-table chest in radius, then report any chest whose
  // post-unpack contents include something other than minecraft:bedrock. Each
  // chest gets a colored highlight indicator: green (interesting non-bedrock
  // contents), white (bedrock-only placeholder), gray (empty), red (errored).
  event.register(
    CHEST_AUDIT_MC.Commands.literal('audit_loot_scan')
      .requires(src => src.hasPermission(2))
      .executes(ctx => auditLootScan(ctx.source, 32))
      .then(
        CHEST_AUDIT_MC.Commands.argument('radius', CHEST_AUDIT_MC.IntArg.integer(1, 128))
          .executes(ctx => auditLootScan(ctx.source, CHEST_AUDIT_MC.IntArg.getInteger(ctx, 'radius')))
      )
  )
})

// ----------------------------- Top-level entries ----------------------------

function auditPlayerChest(src, unpack) {
  const player = src.player
  if (!player) {
    src.sendFailure(Text.of('Must be run by a player.'))
    return 0
  }
  return auditChestAt(src, player.getBlockX(), player.getBlockY(), player.getBlockZ(), unpack)
}

function auditNearbyChests(src, radius, unpack) {
  const player = src.player
  if (!player) {
    src.sendFailure(Text.of('Must be run by a player.'))
    return 0
  }
  return auditNearbyChestsAt(src, player.getBlockX(), player.getBlockY(), player.getBlockZ(), radius, unpack)
}

function auditChestTarget(src, unpack) {
  const player = src.player
  if (!player) {
    src.sendFailure(Text.of('Must be run by a player.'))
    return 0
  }
  let hit
  try {
    // pick(distance, partialTicks, hitFluids)
    hit = player.pick(8.0, 0.0, false)
  } catch (e) {
    return reportException(src, 'player.pick', e)
  }
  if (!hit) {
    src.sendFailure(Text.of('§cNo block targeted within 8 blocks.§r'))
    return 0
  }
  let typ
  try { typ = hit.getType() } catch (e) { return reportException(src, 'hit.getType', e) }
  if (typ !== CHEST_AUDIT_MC.HitResultType.BLOCK) {
    src.sendFailure(Text.of(`§cNo block targeted (hit type=${typ}).§r`))
    return 0
  }
  const pos = hit.getBlockPos()
  return auditChestAt(src, pos.getX(), pos.getY(), pos.getZ(), unpack)
}

function auditNearbyChestsAt(src, centerX, centerY, centerZ, radius, unpack) {
  // Top-level wrapper so we ALWAYS surface what threw, instead of letting
  // Brigadier swallow it as "An unexpected error occurred trying to execute
  // that command".
  let result = 0
  try {
    result = doAuditNearbyChestsAt(src, centerX, centerY, centerZ, radius, unpack)
  } catch (e) {
    logError('auditNearbyChestsAt top-level', e)
    try { src.sendFailure(Text.of(`§cauditNearbyChestsAt threw: ${stringifyErr(e)}§r`)) } catch (_) {}
  }
  return result
}

function doAuditNearbyChestsAt(src, centerX, centerY, centerZ, radius, unpack) {
  const player = src.player
  const level = resolveLevel(src)
  if (!level) {
    src.sendFailure(Text.of('§cNo level on command source.§r'))
    return 0
  }

  const chunkRadius = (radius >> 4) + 1
  const centerCX = centerX >> 4
  const centerCZ = centerZ >> 4

  let chunksScanned = 0
  let beSeen = 0
  let beContainers = 0
  const found = []

  console.log(`[chest-audit] scan start: center=[${centerX},${centerY},${centerZ}] r=${radius} unpack=${unpack} chunkR=${chunkRadius}`)

  for (let cx = centerCX - chunkRadius; cx <= centerCX + chunkRadius; cx++) {
    for (let cz = centerCZ - chunkRadius; cz <= centerCZ + chunkRadius; cz++) {
      let chunk
      try { chunk = level.getChunk(cx, cz) } catch (e) { logError('getChunk', e); continue }
      if (!chunk) continue
      chunksScanned++

      let beMap
      try { beMap = chunk.getBlockEntities() } catch (e) { logError('getBlockEntities', e); continue }
      if (!beMap) continue

      let it
      try { it = beMap.entrySet().iterator() } catch (e) { logError('entrySet().iterator()', e); continue }
      // KubeJS Rhino bug: `const X = expr` and `let X = expr` inside a while-
      // loop body throw "redeclaration of var X" on iteration 2. Declarations
      // must use `let X` with no initializer (Rhino tolerates that). Hoist
      // every binding here and assign inside the body.
      while (it.hasNext()) {
        let mapEntry, pos, be, x, y, z, blockId, chestResult
        try {
          mapEntry = it.next()
          pos = mapEntry.getKey()
          be = mapEntry.getValue()
          x = pos.getX(); y = pos.getY(); z = pos.getZ()
        } catch (e) { logError('iter.next()', e); continue }
        beSeen++

        if (Math.abs(x - centerX) > radius) continue
        if (Math.abs(y - centerY) > radius) continue
        if (Math.abs(z - centerZ) > radius) continue
        if (!isContainer(be)) continue

        beContainers++
        blockId = getBlockIdAt(level, pos)
        chestResult = null
        // One bad chest must not crash the whole scan.
        try {
          chestResult = inspectChest(level, player, be, pos, blockId, unpack)
        } catch (e) {
          logError(`inspectChest @ [${x},${y},${z}]`, e)
          try { src.sendSystemMessage(Text.of(`§c[${x},${y},${z}] inspectChest threw: ${stringifyErr(e)}§r`)) } catch (_) {}
          continue
        }
        if (chestResult) {
          found.push(chestResult)
          try { visualizeChest(level, src, pos, chestResult.particleKind) } catch (e) { logError('visualizeChest', e) }
        }
      }
    }
  }

  try {
    src.sendSystemMessage(Text.of(`§eChest audit§r center=[${centerX},${centerY},${centerZ}] r=${radius} mode=${unpack ? '§cUNPACK§r' : '§apeek§r'} | chunks=${chunksScanned} BEs=${beSeen} containers=${beContainers} reported=${found.length}`))
  } catch (e) { logError('summary message', e) }

  // Indexed loop, NOT for-of: KubeJS Rhino's older mode handles `for...of`
  // unreliably and chokes on per-iteration `const` re-declaration.
  for (let i = 0; i < found.length; i++) {
    try {
      sendChestEntry(src, found[i])
    } catch (e) {
      logError(`sendChestEntry @ ${found[i].x},${found[i].y},${found[i].z}`, e)
      try { src.sendSystemMessage(Text.of(`§csendChestEntry threw: ${stringifyErr(e)}§r`)) } catch (_) {}
    }
  }

  console.log(`[chest-audit] scan done: chunks=${chunksScanned} BEs=${beSeen} containers=${beContainers} reported=${found.length}`)
  return found.length
}

// ----------------------------------------------------------------------------
// /audit_loot_scan [radius]
//
// 1. Find every container BE in radius (chunk.getBlockEntities() iteration).
// 2. If it has an active loot table, capture {table, seed} then call
//    unpackLootTable(player) — destructively fills the chest.
// 3. Read post-unpack contents.
// 4. If contents have anything other than minecraft:bedrock, report it with
//    the captured loot table. Bedrock-only and empty chests stay quiet.
// 5. Highlight every found chest:
//      green  = interesting (non-bedrock content)
//      white  = bedrock-only placeholder
//      gray   = empty
//      red    = inspection error
// ----------------------------------------------------------------------------
function auditLootScan(src, radius) {
  let result = 0
  try {
    result = doAuditLootScan(src, radius)
  } catch (e) {
    logError('auditLootScan top-level', e)
    try { src.sendFailure(Text.of(`§cauditLootScan threw: ${stringifyErr(e)}§r`)) } catch (_) {}
  }
  return result
}

function doAuditLootScan(src, radius) {
  const player = src.player
  if (!player) {
    src.sendFailure(Text.of('Must be run by a player.'))
    return 0
  }
  const level = resolveLevel(src)
  if (!level) {
    src.sendFailure(Text.of('§cNo level on command source.§r'))
    return 0
  }

  const centerX = player.getBlockX()
  const centerY = player.getBlockY()
  const centerZ = player.getBlockZ()
  const chunkRadius = (radius >> 4) + 1
  const centerCX = centerX >> 4
  const centerCZ = centerZ >> 4

  let chunksScanned = 0
  let containersFound = 0
  let unpackedCount = 0
  let interestingCount = 0
  let bedrockOnlyCount = 0
  let emptyCount = 0
  let errorCount = 0
  const interesting = []   // collected only for the post-pass chat report
  const runId = nextRunId()  // unique id for this scan's spawned highlight markers

  console.log(`[chest-audit] loot-scan start: center=[${centerX},${centerY},${centerZ}] r=${radius} runId=${runId}`)

  for (let cx = centerCX - chunkRadius; cx <= centerCX + chunkRadius; cx++) {
    for (let cz = centerCZ - chunkRadius; cz <= centerCZ + chunkRadius; cz++) {
      let chunk
      try { chunk = level.getChunk(cx, cz) } catch (e) { logError('getChunk', e); continue }
      if (!chunk) continue
      chunksScanned++

      let beMap
      try { beMap = chunk.getBlockEntities() } catch (e) { logError('getBlockEntities', e); continue }
      if (!beMap) continue

      let it
      try { it = beMap.entrySet().iterator() } catch (e) { logError('entrySet().iterator()', e); continue }

      while (it.hasNext()) {
        // No-init declarations only — see Rhino-redeclaration notes elsewhere in this file.
        let mapEntry, pos, be, x, y, z, blockId, beforeTable, beforeSeed, didUnpack, contents, kind, errMsg
        try {
          mapEntry = it.next()
          pos = mapEntry.getKey()
          be = mapEntry.getValue()
          x = pos.getX(); y = pos.getY(); z = pos.getZ()
        } catch (e) { logError('iter.next()', e); continue }

        if (Math.abs(x - centerX) > radius) continue
        if (Math.abs(y - centerY) > radius) continue
        if (Math.abs(z - centerZ) > radius) continue
        if (!isContainer(be)) continue

        containersFound++
        blockId = getBlockIdAt(level, pos)
        beforeTable = null
        beforeSeed = null
        didUnpack = false
        contents = []
        kind = 'empty'
        errMsg = null

        // Capture the loot table key BEFORE we touch anything destructive.
        // be.getLootTable() is non-destructive (returns the key field).
        // `let lootKey` (no init) hoisted out of the try — same Rhino const-in-try
        // redeclaration trap as elsewhere.
        let lootKey = null
        try { lootKey = be.getLootTable() } catch (_) { /* not a RandomizableContainer — fine */ }
        if (lootKey) {
          try {
            beforeTable = String(lootKey.location())
            beforeSeed = be.getLootTableSeed()
          } catch (_) {}
        }

        // Force unpack if a loot table is set. This calls into vanilla's
        // unpackLootTable which fills the BE and clears the LootTable tag.
        if (beforeTable) {
          try {
            be.unpackLootTable(player)
            be.setChanged()
            didUnpack = true
            unpackedCount++
          } catch (e) {
            logError(`unpackLootTable @ [${x},${y},${z}]`, e)
            errMsg = appendErr(errMsg, 'unpack:' + stringifyErr(e))
          }
        }

        // Now read what's actually in there. After unpack (or if there was no
        // loot table), getItem() is non-destructive.
        try {
          contents = summarizeContainer(be)
        } catch (e) {
          logError(`summarize @ [${x},${y},${z}]`, e)
          errMsg = appendErr(errMsg, 'summarize:' + stringifyErr(e))
        }

        // Classify. The user's filter rule:
        //   show in chat & highlight ONLY if the chest is non-empty AND
        //   contains zero bedrock items.
        // Empty chests, error chests, and any chest containing bedrock
        // (even if it also has other items) are silently skipped.
        if (errMsg) {
          kind = 'error'
          errorCount++
        } else if (contents.length === 0) {
          kind = 'empty'
          emptyCount++
        } else if (containsBedrock(contents)) {
          kind = 'static'
          bedrockOnlyCount++
        } else {
          kind = 'loot'
          interestingCount++
          interesting.push({
            x: x, y: y, z: z,
            blockId: blockId,
            beforeTable: beforeTable,
            beforeSeed: beforeSeed,
            didUnpack: didUnpack,
            contents: contents,
            errMsg: errMsg,
          })
          // Visualize ONLY interesting chests, so the world isn't littered
          // with markers around bedrock placeholders / empties.
          try { visualizeChest(level, src, pos, kind) } catch (e) { logError('visualizeChest', e) }
          try { summonHighlightShulker(src, pos, runId, kind) } catch (e) { logError('summonHighlightShulker', e) }
        }
      }
    }
  }

  // Queue cleanup once for the whole run.
  try { queueHighlightCleanup(level.getServer(), runId) } catch (e) { logError('queueHighlightCleanup', e) }

  // Summary line.
  try {
    src.sendSystemMessage(Text.of(
      `§eLoot scan§r r=${radius} | chunks=${chunksScanned} containers=${containersFound} unpacked=${unpackedCount} | ` +
      `§ainteresting=${interestingCount}§r §7withBedrock=${bedrockOnlyCount} empty=${emptyCount}§r${errorCount > 0 ? ` §cerrors=${errorCount}§r` : ''}`
    ))
  } catch (e) { logError('loot-scan summary', e) }

  // Per-interesting-chest report. Each line is sent via /tellraw — that path
  // goes through vanilla MC's command system and is the same one /tellraw
  // uses when typed by a player, so click events render reliably.
  // NOTE: variable named `report` (NOT `e`). KubeJS Rhino hoists `catch (e)`
  // bindings to function scope, so a `const X = ...` in a loop body collides
  // with every prior catch arm in this function and throws "redeclaration of
  // var X". Use a name that doesn't clash with any catch parameter.
  for (let i = 0; i < interesting.length; i++) {
    let report, headerJson
    report = interesting[i]
    try {
      // Plain-text line: ALWAYS sent, ALWAYS readable, includes the literal
      // /tp command for copy-paste even if click events don't fire.
      try { src.sendSystemMessage(Text.of(buildLootScanLinePlainText(report))) } catch (e) { logError('plain header', e) }

      // Clickable [TP] line via /tellraw — best-effort. Click uses
      // suggest_command, so even on servers that block run_command clicks
      // the chat input gets pre-filled with /tp.
      try {
        headerJson = buildLootScanLineJson(report, buildTableLabelJsonParts(report))
        sendTellraw(src, headerJson)
      } catch (e) { logError('tellraw header', e) }

      try {
        src.sendSystemMessage(Text.of('    §fcontents§r: ' + report.contents.join(', ')))
      } catch (e) { logError('contents line', e) }

      if (report.errMsg) {
        try { src.sendSystemMessage(Text.of('    §cerror: ' + report.errMsg + '§r')) } catch (e) { logError('error line', e) }
      }
    } catch (err) { logError('loot-scan report', err) }
  }

  console.log(`[chest-audit] loot-scan done: ${interestingCount} interesting / ${containersFound} containers (${unpackedCount} unpacked)`)
  return interestingCount
}

// True iff any entry in contents is a bedrock stack ("<n>x minecraft:bedrock").
// Used to *exclude* placeholder chests from the loot scan report.
function containsBedrock(contents) {
  if (contents.length === 0) return false
  let entry  // hoisted no-init — see Rhino-redeclaration notes elsewhere.
  for (let i = 0; i < contents.length; i++) {
    entry = contents[i]
    if (entry && endsWithBedrock(entry)) return true
  }
  return false
}

function endsWithBedrock(s) {
  // Avoid String.endsWith reliance — older Rhino is fussy.
  const suffix = ' minecraft:bedrock'
  return s.length >= suffix.length && s.substring(s.length - suffix.length) === suffix
}

function auditChestAt(src, x, y, z, unpack) {
  const player = src.player
  const level = resolveLevel(src)
  if (!level) {
    src.sendFailure(Text.of('§cNo level on command source.§r'))
    return 0
  }

  const pos = new CHEST_AUDIT_MC.BlockPos(x, y, z)
  let state
  try { state = level.getBlockState(pos) } catch (e) { return reportException(src, 'getBlockState', e) }
  const blockId = getBlockIdFromState(state)
  src.sendSystemMessage(Text.of(`§eAudit§r [${x},${y},${z}] block=§b${blockId}§r state=§7${String(state)}§r`))

  let be
  try { be = level.getBlockEntity(pos) } catch (e) { return reportException(src, 'getBlockEntity', e) }
  if (!be) {
    src.sendSystemMessage(Text.of('  §cNo block entity at that position.§r'))
    try { visualizeChest(level, src, pos, 'error') } catch (e) { logError('visualizeChest', e) }
    return 0
  }

  src.sendSystemMessage(Text.of(`  BE id=§7${getBlockEntityId(be)}§r class=§8${getClassName(be)}§r`))
  let isRand = '?'
  let isCont = '?'
  try { isRand = String(CHEST_AUDIT_MC.RandomizableContainer.isInstance(be)) } catch (_) {
    // Fall back to method-existence check.
    try { be.getLootTable(); isRand = 'true (method exists)' } catch (_) { isRand = 'false' }
  }
  try { isCont = String(CHEST_AUDIT_MC.Container.isInstance(be)) } catch (_) {
    try { isCont = String(be.getContainerSize() > 0) } catch (_) { isCont = '?' }
  }
  src.sendSystemMessage(Text.of(`  RandomizableContainer? §7${isRand}§r  Container? §7${isCont}§r`))

  if (!isContainer(be)) {
    src.sendSystemMessage(Text.of('  §cBlock entity is not a Container.§r'))
    try { visualizeChest(level, src, pos, 'error') } catch (e) { logError('visualizeChest', e) }
    return 0
  }

  let entry = null
  try {
    entry = inspectChest(level, player, be, pos, blockId, unpack)
  } catch (e) {
    return reportException(src, `inspectChest @ [${x},${y},${z}]`, e)
  }
  if (!entry) return 0
  try { sendChestEntry(src, entry) } catch (e) { reportException(src, 'sendChestEntry', e) }
  try { visualizeChest(level, src, pos, entry.particleKind) } catch (e) { logError('visualizeChest', e) }
  return 1
}

function dumpChestNbtAt(src, x, y, z) {
  const level = resolveLevel(src)
  if (!level) { src.sendFailure(Text.of('§cNo level on command source.§r')); return 0 }

  const pos = new CHEST_AUDIT_MC.BlockPos(x, y, z)
  let be
  try { be = level.getBlockEntity(pos) } catch (e) { return reportException(src, 'getBlockEntity', e) }
  if (!be) {
    src.sendSystemMessage(Text.of(`§cNo block entity at [${x},${y},${z}].§r`))
    return 0
  }

  src.sendSystemMessage(Text.of(`§eNBT§r [${x},${y},${z}] id=§7${getBlockEntityId(be)}§r class=§8${getClassName(be)}§r`))

  let provider
  try { provider = level.registryAccess() } catch (e) { return reportException(src, 'level.registryAccess()', e) }

  // NOTE: avoid `const`/`let` inside these try-blocks. KubeJS's Rhino keeps a
  // failed `const X = expr` binding alive across function calls, so the second
  // invocation throws "redeclaration of var X" instead of running the body.
  // Hoist nbtStr out and just assign.
  let nbtStr = null
  try {
    nbtStr = String(be.saveWithFullMetadata(provider))
  } catch (e) {
    reportException(src, 'saveWithFullMetadata', e)
  }
  if (nbtStr == null) {
    try {
      // saveAdditional is protected; saveCustomOnly is the public alternative.
      nbtStr = String(be.saveCustomOnly(provider))
    } catch (e2) {
      reportException(src, 'saveCustomOnly', e2)
    }
  }

  if (nbtStr == null) {
    src.sendSystemMessage(Text.of('  §cFailed to serialize NBT — see logs/kubejs/server.log.§r'))
    return 0
  }

  console.log(`[chest-audit] NBT dump for [${x},${y},${z}] (${getClassName(be)}):\n${nbtStr}`)
  const chunkSize = 220
  const maxChunks = 6
  for (let i = 0; i < nbtStr.length && i < maxChunks * chunkSize; i += chunkSize) {
    src.sendSystemMessage(Text.of('  ' + nbtStr.substring(i, Math.min(nbtStr.length, i + chunkSize))))
  }
  if (nbtStr.length > maxChunks * chunkSize) {
    src.sendSystemMessage(Text.of(`  §7...truncated (${nbtStr.length} chars total). Full dump in logs/kubejs/server.log.§r`))
  }
  return 1
}

// ----------------------------- Inspection core ------------------------------

function inspectChest(level, player, be, pos, blockId, unpack) {
  const entry = {
    x: pos.getX(), y: pos.getY(), z: pos.getZ(),
    blockId: blockId,
    beClass: getClassName(be),
    beId: getBlockEntityId(be),
    isRandomizable: false,
    lootTable: null,
    lootTableSeed: null,
    containerSize: -1,
    currentContents: null,   // null = "not read (loot table still set; reading would consume)"
    peeked: null,
    unpacked: false,
    contentsAfterUnpack: null,
    error: null,
    particleKind: 'empty',
  }

  try { entry.containerSize = be.getContainerSize() } catch (e) {
    logError('getContainerSize', e); entry.error = appendErr(entry.error, 'size:' + e)
  }

  // Detect RandomizableContainer by trying the method, NOT by Class.isInstance —
  // KubeJS's wrapper around BEs returned from chunk.getBlockEntities() confuses
  // Class.isInstance, but the method dispatch still works.
  let lootKey = null
  let canRandomize = false
  try {
    lootKey = be.getLootTable()  // returns null on randomizable chests with no table
    canRandomize = true
  } catch (e) {
    // Not a RandomizableContainer (or method missing).
    canRandomize = false
  }
  entry.isRandomizable = canRandomize

  if (lootKey) {
    try {
      entry.lootTable = String(lootKey.location())
      entry.lootTableSeed = be.getLootTableSeed()
    } catch (e) {
      logError('lootKey.location/seed', e); entry.error = appendErr(entry.error, 'lootKey:' + e)
    }
  }

  // CRITICAL: RandomizableContainerBlockEntity.getItem(int) internally calls
  // unpackLootTable(null), which generates loot AND clears the LootTable tag.
  // So reading slots on an unconsumed loot-table chest is destructive. We use
  // peekLoot (non-destructive) when a loot table is set, and only call the
  // slot-reading summarizeContainer when there's no loot table to lose.
  if (entry.lootTable) {
    try { entry.peeked = peekLoot(level, be, pos) } catch (e) {
      logError('peekLoot', e); entry.error = appendErr(entry.error, 'peek:' + e)
    }
    // currentContents intentionally left null — it would consume the loot table.
  } else {
    try { entry.currentContents = summarizeContainer(be) } catch (e) {
      logError('summarize-before', e); entry.error = appendErr(entry.error, 'sumBefore:' + e)
    }
  }

  if (unpack && entry.lootTable) {
    try {
      be.unpackLootTable(player)
      be.setChanged()
      entry.unpacked = true
    } catch (e) {
      logError('unpackLootTable', e); entry.error = appendErr(entry.error, 'unpack:' + e)
    }
    try { entry.contentsAfterUnpack = summarizeContainer(be) } catch (e) {
      logError('summarize-after', e); entry.error = appendErr(entry.error, 'sumAfter:' + e)
    }
  }

  if (entry.error) entry.particleKind = 'error'
  else if (entry.lootTable) entry.particleKind = 'loot'
  else if (entry.currentContents && entry.currentContents.length > 0) entry.particleKind = 'static'
  else entry.particleKind = 'empty'

  return entry
}

function peekLoot(level, container, pos) {
  let server
  try { server = level.getServer() } catch (e) { logError('level.getServer()', e); return null }
  if (!server) {
    console.warn('[chest-audit] peekLoot: level.getServer() == null')
    return null
  }
  const key = container.getLootTable()
  if (!key) return null
  const lootTable = server.reloadableRegistries().getLootTable(key)
  if (!lootTable) {
    console.warn(`[chest-audit] peekLoot: no LootTable in registry for ${key}`)
    return null
  }

  // We don't use ServerLevel.isInstance(level) here — KubeJS Rhino sometimes
  // refuses to forward Class.isInstance for concrete classes ("ServerLevel
  // has no method isInstance"). The level.getServer() != null check above
  // already implies we're on a ServerLevel; if we're wrong, the LootParams
  // constructor will throw and our caller's try/catch will report it.
  // Hoisted out of try — same Rhino const-in-try redeclaration trap.
  let params = null
  let builder = null
  try {
    builder = new CHEST_AUDIT_MC.LootParamsBuilder(level)
      .withParameter(CHEST_AUDIT_MC.LootContextParams.ORIGIN, CHEST_AUDIT_MC.Vec3.atCenterOf(pos))
    params = builder.create(CHEST_AUDIT_MC.LootContextParamSets.CHEST)
  } catch (e) { logError('LootParamsBuilder', e); return null }

  const seed = container.getLootTableSeed()
  let list = null
  try { list = lootTable.getRandomItems(params, seed) } catch (e) { logError('getRandomItems', e); return null }

  const counts = new Map()
  if (list) {
    const it = list.iterator()
    // Same Rhino while-loop redeclaration trap: hoist with no-init.
    while (it.hasNext()) {
      let stack, id
      stack = it.next()
      if (!stack || stack.isEmpty()) continue
      id = getItemId(stack)
      counts.set(id, (counts.get(id) || 0) + stack.getCount())
    }
  }
  return Array.from(counts.entries())
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(e => `${e[1]}x ${e[0]}`)
}

function summarizeContainer(container) {
  const counts = new Map()
  const size = container.getContainerSize()
  // for(let i=...) is special-cased by Rhino (per-iteration `i`), but `const X
  // = expr` inside the body still hits the redeclaration trap on iteration 2.
  // Hoist with no-init.
  for (let slot = 0; slot < size; slot++) {
    let stack, id
    stack = container.getItem(slot)
    if (!stack || stack.isEmpty()) continue
    id = getItemId(stack)
    counts.set(id, (counts.get(id) || 0) + stack.getCount())
  }
  return Array.from(counts.entries())
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(e => `${e[1]}x ${e[0]}`)
}

// ----------------------------- Output formatting ----------------------------

function sendChestEntry(src, e) {
  const tableLabel = e.lootTable
    ? `§atable§r=§e${e.lootTable}§r seed=§7${e.lootTableSeed}§r`
    : '§7table=n/a§r'
  const sizeLabel = e.containerSize >= 0 ? `slots=${e.containerSize}` : 'slots=?'

  src.sendSystemMessage(Text.of(`§7[${e.x},${e.y},${e.z}]§r §b${e.blockId}§r ${sizeLabel} | ${tableLabel}`))
  src.sendSystemMessage(Text.of(`  BE id=§7${e.beId}§r class=§8${e.beClass}§r randomizable=${e.isRandomizable}`))

  if (e.peeked !== null) {
    const label = e.peeked.length ? '§apeek (would generate)§r' : '§6peek (loot table generated 0 items)§r'
    src.sendSystemMessage(Text.of(`  ${label}: ${e.peeked.length ? e.peeked.join(', ') : '<empty roll>'}`))
  }
  if (e.currentContents === null) {
    src.sendSystemMessage(Text.of(`  §7current contents: <not read — reading triggers unpack; use the 'unpack' subcommand to commit>§r`))
  } else {
    src.sendSystemMessage(Text.of(`  §fcurrent contents§r: ${e.currentContents.length ? e.currentContents.join(', ') : '<empty>'}`))
  }
  if (e.unpacked && e.contentsAfterUnpack) {
    src.sendSystemMessage(Text.of(`  §dafter unpack§r: ${e.contentsAfterUnpack.length ? e.contentsAfterUnpack.join(', ') : '<empty>'}`))
  }
  if (e.error) {
    src.sendSystemMessage(Text.of(`  §cerror: ${e.error}§r`))
  }
}

// --------------------------- Visual debug particles -------------------------

const PARTICLE_FOR_KIND = {
  loot:   () => CHEST_AUDIT_MC.ParticleTypes.SOUL_FIRE_FLAME,    // bright cyan
  static: () => CHEST_AUDIT_MC.ParticleTypes.END_ROD,            // bright white streaks
  empty:  () => CHEST_AUDIT_MC.ParticleTypes.SMOKE,
  error:  () => CHEST_AUDIT_MC.ParticleTypes.FLAME,              // orange
}

// Glow color override for the BlockDisplay outline (RGB int).
const GLOW_COLOR_FOR_KIND = {
  loot:   0x00FF55,   // green: unconsumed loot table
  static: 0xFFFFFF,   // white: has items, no loot table
  empty:  0x808080,   // gray: empty
  error:  0xFF3333,   // red: error
}

const VIS_DURATION_TICKS = 400  // 20 seconds — long enough to walk to a found chest

// Maps audit kind → ChatFormatting enum name. Used as the team color, which is
// what the vanilla glow renderer uses to color the through-walls outline.
const TEAM_COLOR_FOR_KIND = {
  loot:   'GREEN',
  static: 'WHITE',
  empty:  'GRAY',
  error:  'RED',
}

// Get-or-create the per-kind scoreboard team. Teams persist in the level save,
// but they're tiny — four entries total — and we re-use them across runs.
function ensureKindTeam(level, kind) {
  let sb = null
  try { sb = level.getScoreboard() } catch (e) { logError('level.getScoreboard()', e); return null }
  if (!sb) return null

  const teamName = 'audit_' + kind
  let team = null
  try { team = sb.getPlayerTeam(teamName) } catch (_) {}
  if (team) return { sb: sb, team: team, name: teamName }

  try { team = sb.addPlayerTeam(teamName) } catch (e) { logError('addPlayerTeam ' + teamName, e); return null }
  if (!team) return null

  // Set the team color via ChatFormatting.<NAME>.
  try {
    const fmtName = TEAM_COLOR_FOR_KIND[kind] || 'WHITE'
    const formatting = CHEST_AUDIT_MC.ChatFormatting.valueOf(fmtName)
    team.setColor(formatting)
  } catch (e) { logError('setColor', e) }

  // We don't want team-mates to see our invisible markers as visible.
  try { team.setSeeFriendlyInvisibles(false) } catch (_) {}

  return { sb: sb, team: team, name: teamName }
}

function visualizeChest(level, src, pos, kind) {
  // We deliberately do NOT use ServerLevel.isInstance(level) here — KubeJS
  // wraps server levels in a way that breaks Class.isInstance. Instead we just
  // try operations; failures get logged and the rest of the function continues.
  const cx = pos.getX() + 0.5
  const cy = pos.getY()
  const cz = pos.getZ() + 0.5
  const supplier = PARTICLE_FOR_KIND[kind] || PARTICLE_FOR_KIND.empty
  const particle = supplier()
  const glowColor = GLOW_COLOR_FOR_KIND[kind] || 0xFFFFFF

  // 1) Force-target a particle burst at the command-issuing player so it
  //    shows even if they're far away or have particle settings turned down.
  let player = null
  try { player = src && src.player } catch (_) {}
  try {
    let yi
    for (let i = 0; i < 6; i++) {
      yi = cy + 0.5 + i * 0.4
      if (player) {
        level.sendParticles(player, particle, true, cx, yi, cz, 12, 0.2, 0.05, 0.2, 0.02)
      } else {
        level.sendParticles(particle, cx, yi, cz, 12, 0.2, 0.05, 0.2, 0.02)
      }
    }
    if (player) {
      level.sendParticles(player, particle, true, cx, cy + 3.5, cz, 40, 0.4, 0.0, 0.4, 0.05)
    } else {
      level.sendParticles(particle, cx, cy + 3.5, cz, 40, 0.4, 0.0, 0.4, 0.05)
    }
  } catch (e) { logError('sendParticles burst', e) }

  // 2) Vertical column of AreaEffectClouds — emits the kind particle every
  //    tick, so a beacon-like beam is visible from above for VIS_DURATION.
  let aec
  for (let dy = 0; dy < 6; dy++) {
    try {
      aec = new CHEST_AUDIT_MC.AreaEffectCloud(level, cx, cy + 0.5 + dy * 0.7, cz)
      aec.setRadius(0.5)
      aec.setDuration(VIS_DURATION_TICKS)
      aec.setWaitTime(0)
      aec.setRadiusPerTick(0)
      aec.setRadiusOnUse(0)
      aec.setParticle(particle)
      level.addFreshEntity(aec)
    } catch (e) { logError('AreaEffectCloud', e) }
  }

  // 3) THROUGH-WALLS HIGHLIGHT.
  //    Spawn an invisible, AI-disabled Shulker with the Glowing tag set, and
  //    add it to a per-kind scoreboard team. Vanilla's glow renderer draws the
  //    entity bounding-box outline through any block, in the team color. The
  //    shulker's hitbox is 1×1×1 — same shape as the chest, so the outline is
  //    a chest-sized box exactly where the chest is. This is the same trick
  //    spectral arrows use to highlight mobs through walls, just stationary.
  let shulker = null
  let teamInfo = null
  try {
    shulker = CHEST_AUDIT_MC.EntityType.SHULKER.create(level)
    if (shulker) {
      shulker.setPos(pos.getX() + 0.5, pos.getY(), pos.getZ() + 0.5)
      shulker.setNoAi(true)
      shulker.setSilent(true)
      shulker.setInvulnerable(true)
      shulker.setInvisible(true)
      shulker.setNoGravity(true)
      shulker.setGlowingTag(true)
      level.addFreshEntity(shulker)

      // Team membership controls the outline color.
      teamInfo = ensureKindTeam(level, kind)
      if (teamInfo) {
        try { teamInfo.sb.addPlayerToTeam(shulker.getStringUUID(), teamInfo.team) } catch (e) { logError('addPlayerToTeam', e) }
      }
    }
  } catch (e) { logError('Shulker through-walls marker', e) }

  // 4) BlockDisplay clone of the chest with explicit glowColorOverride. This
  //    is a backup highlight (some clients/servers don't render entity glow
  //    on a non-mob entity at high view-range, so we keep both). Crank up the
  //    view range so it isn't culled at distance.
  let display = null
  try {
    let blockState = null
    try { blockState = level.getBlockState(pos) } catch (_) {}
    display = CHEST_AUDIT_MC.EntityType.BLOCK_DISPLAY.create(level)
    if (display) {
      display.setPos(pos.getX(), pos.getY(), pos.getZ())
      display.setGlowingTag(true)
      if (blockState && _DISP_REFL.setBlockState) {
        try { _DISP_REFL.setBlockState.invoke(display, blockState) } catch (e) { logError('reflect setBlockState', e) }
      }
      if (_DISP_REFL.setGlowColor) {
        try { _DISP_REFL.setGlowColor.invoke(display, glowColor) } catch (e) { logError('reflect setGlowColor', e) }
      }
      if (_DISP_REFL.setViewRange) {
        try { _DISP_REFL.setViewRange.invoke(display, 10.0) } catch (e) { logError('reflect setViewRange', e) }
      }
      level.addFreshEntity(display)
    }
  } catch (e) { logError('block display marker', e) }

  // 5) Schedule cleanup of the spawned entities at expireAt. Failure is
  //    survivable — at worst the markers persist until the chunk unloads.
  let server = null
  try { server = level.getServer() } catch (_) {}
  if (server) {
    let now = 0
    try { now = server.getTickCount() } catch (_) {}
    const expireAt = now + VIS_DURATION_TICKS
    const cleanupShulker = shulker
    const cleanupDisplay = display
    const cleanupTeam = teamInfo
    let task = null  // hoisted to dodge the Rhino const-in-try redeclaration trap
    try {
      task = new CHEST_AUDIT_MC.TickTask(expireAt, function () {
        try {
          if (cleanupShulker) {
            if (cleanupTeam) {
              try { cleanupTeam.sb.removePlayerFromTeam(cleanupShulker.getStringUUID(), cleanupTeam.team) } catch (_) {}
            }
            cleanupShulker.discard()
          }
        } catch (_) {}
        try { if (cleanupDisplay) cleanupDisplay.discard() } catch (_) {}
      })
      server.tell(task)
    } catch (e) { logError('server.tell cleanup', e) }
  }
}

// -------------------------------- Helpers -----------------------------------

function resolveLevel(src) {
  try {
    if (src.level) return src.level
  } catch (_) {}
  try { return src.getLevel() } catch (_) {}
  try { return src.player.level } catch (_) {}
  try { return src.player.level() } catch (_) {}
  return null
}

function isContainer(be) {
  if (!be) return false
  try { return CHEST_AUDIT_MC.Container.isInstance(be) } catch (e) {
    logError('Container.isInstance', e)
    try { return be.getContainerSize() > 0 } catch (_) { return false }
  }
}

function getMinHeight(level) {
  try { return level.getMinBuildHeight() } catch (_) {
    try { return level.getMinY() } catch (_) { return -64 }
  }
}

function getMaxHeight(level) {
  try { return level.getMaxBuildHeight() } catch (_) {
    try { return level.getMaxY() + 1 } catch (_) { return 320 }
  }
}

// KubeJS's Rhino wraps Java objects in a way that often shadows obj.getClass()
// (returns the JS wrapper class, or throws). Reflection bypasses the wrapper.
// Note: NO `const`/`let` inside try-blocks here — see Rhino redeclaration note above.
const _OBJ_GET_CLASS = (function () {
  try { return Java.loadClass('java.lang.Object').getMethod('getClass') } catch (_) { return null }
})()

function getClassName(obj) {
  if (obj == null) return 'null'
  let cls = null
  let s = null
  if (_OBJ_GET_CLASS) {
    try { cls = _OBJ_GET_CLASS.invoke(obj) } catch (_) {}
    if (cls) {
      try { return String(cls.getName()) } catch (_) {}
    }
  }
  try { return String(obj.getClass().getName()) } catch (_) {}
  try { return String(obj['class'].getName()) } catch (_) {}
  try { return String(obj.class.name) } catch (_) {}
  try { s = String(obj) } catch (_) {}
  if (s) {
    let at = -1
    try { at = s.indexOf('@') } catch (_) {}
    return at > 0 ? s.substring(0, at) : s
  }
  return 'unknown'
}

// Resolve "minecraft:chest" / "minecraft:barrel" / etc. from a BE — the registry
// id is more useful in chat than the Java class name. Falls back to class name.
const _BE_TYPE_REG = (function () {
  try { return CHEST_AUDIT_MC.BuiltInRegistries.BLOCK_ENTITY_TYPE } catch (_) { return null }
})()

function getBlockEntityId(be) {
  if (be == null) return 'null'
  if (_BE_TYPE_REG) {
    let type = null
    try { type = be.getType() } catch (_) {}
    if (type) {
      let key = null
      try { key = _BE_TYPE_REG.getKey(type) } catch (_) {}
      if (key) {
        try { return String(key) } catch (_) {}
      }
    }
  }
  return getClassName(be)
}

function getItemId(stack) {
  try { return String(CHEST_AUDIT_MC.BuiltInRegistries.ITEM.getKey(stack.getItem())) } catch (_) {
    return String(stack)
  }
}

function getBlockIdFromState(state) {
  try { return String(CHEST_AUDIT_MC.BuiltInRegistries.BLOCK.getKey(state.getBlock())) } catch (_) {
    try { return String(state.getBlock().builtInRegistryHolder().key().location()) } catch (_) { return String(state) }
  }
}

function getBlockIdAt(level, pos) {
  try { return getBlockIdFromState(level.getBlockState(pos)) } catch (e) {
    logError('getBlockIdAt', e); return '<error>'
  }
}

function appendErr(prev, msg) { return prev ? (prev + '; ' + msg) : msg }

// ----------------------------------------------------------------------------
// Command-dispatcher helpers
//
// These run vanilla /tellraw and /summon through MinecraftServer.getCommands()
// .performPrefixedCommand. They bypass any KubeJS Rhino quirks because the
// command system is what /tellraw and /summon use when a player types them in
// chat — pure vanilla code path.
// ----------------------------------------------------------------------------

let _RUN_ID_COUNTER = 0
function nextRunId() {
  _RUN_ID_COUNTER += 1
  // Tag chars must match [A-Za-z0-9_.+-]; underscore + counter is plenty.
  return 'r' + _RUN_ID_COUNTER + '_' + Date.now()
}

function elevatedSrc(src) {
  // Returns a permission-2 silent variant of the source so commands run
  // without spamming feedback or being denied by permission level.
  let result = src
  try { result = result.withMaximumPermission(2) } catch (_) {}
  try { result = result.withSuppressedOutput() } catch (_) {}
  return result
}

function runServerCommand(src, command) {
  try {
    src.getServer().getCommands().performPrefixedCommand(elevatedSrc(src), command)
    return true
  } catch (e) {
    logError('runServerCommand: ' + command, e)
    return false
  }
}

// Lower-case Minecraft color names for /team modify <team> color <name>.
const TEAM_COLOR_NAME_FOR_KIND = {
  loot:   'green',
  static: 'white',
  empty:  'gray',
  error:  'red',
}

function ensureKindTeamViaCommand(src, kind) {
  const teamName = 'audit_' + kind
  const colorName = TEAM_COLOR_NAME_FOR_KIND[kind] || 'white'
  // /team add is idempotent-safe: it errors if the team exists, but we ignore.
  // We always re-assert the color in case something changed it.
  runServerCommand(src, 'team add ' + teamName)
  runServerCommand(src, 'team modify ' + teamName + ' color ' + colorName)
  return teamName
}

function summonHighlightShulker(src, pos, runId, kind) {
  const runTag = 'audit_marker_' + runId
  const x = pos.getX() + 0.5
  const y = pos.getY()
  const z = pos.getZ() + 0.5

  // Two tags: a generic 'audit_marker' (lets a startup sweep find leftover
  // markers across script reloads) and a runId-specific 'audit_marker_<runId>'
  // (lets per-run cleanup target only this scan's shulkers).
  // PersistenceRequired:1b prevents natural despawn; NoAI:1b stops it from
  // ticking; Invisible+Glowing+NoGravity makes only the colored outline render.
  const summon =
    'summon shulker ' + x + ' ' + y + ' ' + z +
    ' {NoAI:1b,Silent:1b,Invulnerable:1b,Invisible:1b,Glowing:1b,NoGravity:1b,PersistenceRequired:1b,Tags:["audit_marker","' + runTag + '"]}'
  if (!runServerCommand(src, summon)) return

  // Add to the per-kind team so the outline is the right color.
  const teamName = ensureKindTeamViaCommand(src, kind)
  runServerCommand(src,
    'team join ' + teamName + ' @e[type=shulker,tag=' + runTag + ',limit=1,sort=nearest,distance=..2]')
}

// Cleanup queue for highlight shulkers. Polled in ServerEvents.tick rather
// than scheduled via MinecraftServer.tell(TickTask) — the polling path is
// observably more reliable in this KubeJS / NeoForge build (TickTask closures
// don't always fire from JS-defined Runnables).
const _PENDING_CLEANUPS = []   // [{ runTag, expireAt }]
let _CLEANUP_BOOT_SWEPT = false

function queueHighlightCleanup(server, runId) {
  const runTag = 'audit_marker_' + runId
  let now = 0
  try { now = server.getTickCount() } catch (_) {}
  const expireAt = now + VIS_DURATION_TICKS
  _PENDING_CLEANUPS.push({ runTag: runTag, expireAt: expireAt })
  console.log('[chest-audit] queued cleanup runTag=' + runTag + ' expireAt=' + expireAt + ' now=' + now)
}

function runKillForTag(server, tag) {
  // Hoisted out of the try block — see Rhino "redeclaration of var X" notes
  // elsewhere in this file. A `const X = expr` inside a try whose RHS throws
  // gets stuck and the next call fails on declaration.
  let cmdSrc
  try {
    cmdSrc = server.createCommandSourceStack().withSuppressedOutput().withMaximumPermission(2)
    server.getCommands().performPrefixedCommand(cmdSrc, 'kill @e[type=shulker,tag=' + tag + ']')
  } catch (e) {
    try { console.error('[chest-audit] runKillForTag failed: ' + e) } catch (_) {}
  }
}

ServerEvents.tick(event => {
  let server
  try { server = event.server } catch (_) { return }
  if (!server) return

  // First tick after script load: sweep any orphan markers from prior runs
  // (a /reload mid-scan loses _PENDING_CLEANUPS but leaves the entities).
  if (!_CLEANUP_BOOT_SWEPT) {
    _CLEANUP_BOOT_SWEPT = true
    runKillForTag(server, 'audit_marker')
  }

  if (_PENDING_CLEANUPS.length === 0) return

  let now = 0
  try { now = server.getTickCount() } catch (_) {}
  // Iterate backwards so splice doesn't disturb indexing.
  for (let i = _PENDING_CLEANUPS.length - 1; i >= 0; i--) {
    let item
    item = _PENDING_CLEANUPS[i]
    if (item.expireAt > now) continue
    runKillForTag(server, item.runTag)
    console.log('[chest-audit] cleanup ran for runTag=' + item.runTag)
    _PENDING_CLEANUPS.splice(i, 1)
  }
})

// JSON-escape a string for /tellraw payload. Escapes ALL non-printable-ASCII
// because MC's chat font in some configurations renders unknown glyphs as
// solid boxes, and Brigadier parsing of high-codepoint chars inside command
// strings has been flaky historically.
function jsonStr(s) {
  if (s == null) return '""'
  let out = '"'
  const str = String(s)
  let c, cc
  for (let i = 0; i < str.length; i++) {
    c = str.charAt(i)
    cc = c.charCodeAt(0)
    if (c === '\\') out += '\\\\'
    else if (c === '"') out += '\\"'
    else if (c === '\n') out += '\\n'
    else if (c === '\r') out += '\\r'
    else if (c === '\t') out += '\\t'
    else if (cc < 0x20 || cc > 0x7E) out += '\\u' + ('0000' + cc.toString(16)).slice(-4)
    else out += c
  }
  return out + '"'
}

function sendTellraw(src, jsonComponentArrayString) {
  // tellraw target: @s when the source is a player (the typical case for our
  // command handlers). Falls back to player name for non-self sources.
  let target = '@s'
  try { if (!src.player) target = '@a' } catch (_) {}
  runServerCommand(src, 'tellraw ' + target + ' ' + jsonComponentArrayString)
}

// Build the JSON for an interesting-chest report line. The "[TP]" button uses
// suggest_command (puts /tp into the user's chat input for them to press Enter)
// so that even if their server has run_command click events disabled, the user
// still gets a one-press teleport. The full /tp command is also printed in
// plain text on the next line so they can copy it manually if needed.
//
// hoverEvent is omitted entirely: in 1.21.1, hoverEvent.contents must be a
// Component object (not a bare string), and a wrong shape silently breaks
// the whole tellraw command. Click-only is simpler and reliable.
function buildLootScanLineJson(report, tableLabelParts) {
  const tpCmd = '/tp @s ' + report.x + ' ' + (report.y + 1) + ' ' + report.z
  let out = '['
  out += '{"text":"* ","color":"green"}'
  out += ',{"text":' + jsonStr('[' + report.x + ',' + report.y + ',' + report.z + ']') + ',"color":"gray"}'
  out += ',{"text":" [TP]","color":"aqua","bold":true,"underlined":true' +
         ',"clickEvent":{"action":"suggest_command","value":' + jsonStr(tpCmd) + '}}'
  out += ',{"text":" "}'
  out += ',{"text":' + jsonStr(report.blockId) + ',"color":"aqua"}'
  for (let i = 0; i < tableLabelParts.length; i++) {
    out += ',' + tableLabelParts[i]
  }
  out += ']'
  return out
}

// Plain-text fallback for the report line — printed via sendSystemMessage with
// no JSON, no click events. Guaranteed to render readable text and a copyable
// /tp command so the user can teleport even if all chat-link mechanisms fail.
function buildLootScanLinePlainText(report) {
  const tableStr = report.beforeTable
    ? ('table=' + report.beforeTable + ' seed=' + report.beforeSeed + (report.didUnpack ? ' (unpacked)' : ''))
    : 'table=n/a'
  return '§a* §7[' + report.x + ',' + report.y + ',' + report.z + ']§r §b' + report.blockId +
         '§r §a' + tableStr + '§r §7-> /tp @s ' + report.x + ' ' + (report.y + 1) + ' ' + report.z + '§r'
}

function buildTableLabelJsonParts(report) {
  // Returns an array of pre-stringified JSON component fragments to be appended
  // into the loot-scan line. No leading comma — caller adds.
  const parts = []
  if (!report.beforeTable) {
    parts.push('{"text":" table=n/a","color":"gray"}')
    return parts
  }
  parts.push('{"text":" table=","color":"green"}')
  parts.push('{"text":' + jsonStr(report.beforeTable) + ',"color":"yellow"}')
  parts.push('{"text":" seed=","color":"green"}')
  parts.push('{"text":' + jsonStr(String(report.beforeSeed)) + ',"color":"gray"}')
  if (report.didUnpack) {
    parts.push('{"text":" (unpacked)","color":"light_purple"}')
  }
  return parts
}

// Build a clickable, underlined aqua chat label that runs /tp @s on click.
// y+1 so the player lands on top of the chest, not inside it.
//
// We use KubeJS's Text.of(...) chain rather than a vanilla Component because
// chat messages flowing through src.sendSystemMessage(...) appear to lose
// click events when a vanilla MutableComponent is appended into a Text-wrapped
// parent — Text.of carries them all the way to the chat packet.
function buildTpLink(label, x, y, z) {
  const cmd = '/tp @s ' + x + ' ' + (y + 1) + ' ' + z
  try {
    return Text.of(label).aqua().bold(true).underlined(true).click(cmd)
  } catch (e) {
    logError('buildTpLink Text.of path', e)
  }
  // Fallback: vanilla Component (works in some KubeJS configurations).
  try {
    const c = CHEST_AUDIT_MC.Component.literal(label)
    c.withStyle(CHEST_AUDIT_MC.ChatFormatting.AQUA)
    c.withStyle(CHEST_AUDIT_MC.ChatFormatting.UNDERLINE)
    c.withStyle(CHEST_AUDIT_MC.ChatFormatting.BOLD)
    const click = new CHEST_AUDIT_MC.ClickEvent(CHEST_AUDIT_MC.ClickAction.RUN_COMMAND, cmd)
    c.setStyle(c.getStyle().withClickEvent(click))
    return c
  } catch (e) {
    logError('buildTpLink Component path', e)
  }
  return Text.of(label)
}

// Send a chat line to the source. Prefers player.tell(...) when available
// because KubeJS has a more reliable Text→packet path through that method;
// falls back to src.sendSystemMessage otherwise.
function sendChatLine(src, line) {
  try {
    if (src.player && typeof src.player.tell === 'function') {
      src.player.tell(line)
      return
    }
  } catch (_) {}
  try { src.sendSystemMessage(line) } catch (e) { logError('sendSystemMessage', e) }
}

function stringifyErr(e) {
  let msg = null
  try { msg = String(e && (e.message || e)) } catch (_) {}
  return msg || '<inscrutable>'
}

function reportException(src, label, e) {
  logError(label, e)
  let msg = '<inscrutable>'
  try { msg = String(e && (e.message || e)) } catch (_) {}
  try { src.sendFailure(Text.of(`§c[${label}] ${msg}§r`)) } catch (_) {}
  return 0
}

function logError(label, e) {
  let msg = '[chest-audit] ' + label + ': '
  try { msg += String(e && (e.message || e)) } catch (_) { msg += '<inscrutable>' }
  try { console.error(msg) } catch (_) {}
  try { if (e && e.javaException && e.javaException.printStackTrace) e.javaException.printStackTrace() } catch (_) {}
  try { if (e && e.rhinoException && e.rhinoException.printStackTrace) e.rhinoException.printStackTrace() } catch (_) {}
  try { if (e && e.printStackTrace) e.printStackTrace() } catch (_) {}
}

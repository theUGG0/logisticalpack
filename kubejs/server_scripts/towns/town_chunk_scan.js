// Throttled chunk scan: when a player crosses into a new chunk we haven't
// scanned yet, search the chunk for a town_center marker block. On first
// detection: registerTown -> persist + auto-claim + announce.
//
// Scope notes:
//   - PlayerEvents.tick on server_scripts only fires for ServerPlayer, so
//     no `isClientSide` check is needed (and `level.isClientSide` without
//     parens returns the method-reference object which is always truthy —
//     burned an evening on that one).
//   - The lp-prefixed `var` locals avoid Rhino's const-redeclaration trap
//     in named function expressions / repeat-call event handlers.

if (typeof LPTowns === 'undefined') var LPTowns = {}

;(function () {
  const SCAN_INTERVAL = 20  // fires (= ticks at 20 tps = 1s)
  const MARKER = 'logisticalpack:town_center'
  const Y_RANGE = 32

  LPTowns.lastChunkPerPlayer = LPTowns.lastChunkPerPlayer || {}

  var lpScFireCount = 0

  PlayerEvents.tick(event => {
    lpScFireCount++
    if (lpScFireCount % SCAN_INTERVAL !== 0) return

    var lpScPlayer = event.player
    if (!lpScPlayer) return
    var lpScLevel = lpScPlayer.level
    if (!lpScLevel) return

    var lpScPos = lpScPlayer.blockPosition()
    var lpScCx = lpScPos.x >> 4
    var lpScCz = lpScPos.z >> 4
    var lpScKey = lpScCx + '_' + lpScCz
    var lpScUuid = String(lpScPlayer.uuid)

    if (LPTowns.lastChunkPerPlayer[lpScUuid] === lpScKey) return
    LPTowns.lastChunkPerPlayer[lpScUuid] = lpScKey

    if (LPTowns.isRegistered(lpScLevel, lpScCx, lpScCz)) return

    var lpScMinY = lpScPos.y - Y_RANGE
    var lpScMaxY = lpScPos.y + Y_RANGE
    var lpScBaseX = lpScCx << 4
    var lpScBaseZ = lpScCz << 4
    var lpScDx, lpScDz, lpScY, lpScBlock

    for (lpScDx = 0; lpScDx < 16; lpScDx++) {
      for (lpScDz = 0; lpScDz < 16; lpScDz++) {
        for (lpScY = lpScMinY; lpScY < lpScMaxY; lpScY++) {
          lpScBlock = lpScLevel.getBlock(lpScBaseX + lpScDx, lpScY, lpScBaseZ + lpScDz)
          if (lpScBlock.id === MARKER) {
            LPTowns.registerTown(lpScLevel, lpScBlock.pos)
            return
          }
        }
      }
    }
  })

  PlayerEvents.loggedOut(event => {
    delete LPTowns.lastChunkPerPlayer[String(event.player.uuid)]
  })
})()

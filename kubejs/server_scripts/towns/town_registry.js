// Town registry: persistent storage of every registered town.
// Storage path: level.persistentData.logisticalpack_towns[chunkKey] = { ... }
// Also wires the FTB Chunks auto-claim command on registration.
//
// Rhino quirk note: const/let inside named function expressions get
// hoisted to the enclosing IIFE scope and crash on second invocation
// with "redeclaration of var <name>". Inside this file, all
// LPTowns.* functions use only `var` with unique prefixed names.
// Module-level constants (TOWN_DATA_KEY etc.) are fine as const because
// they're declared once at IIFE-evaluation time and never re-entered.

if (typeof LPTowns === 'undefined') var LPTowns = {}

;(function () {
  const TOWN_DATA_KEY = 'logisticalpack_towns'
  const CLAIM_RADIUS_BLOCKS = 128 // 8-chunk radius

  // Cached short name from the FTB Teams API; populated lazily.
  var lpCachedClaimTeamShortName = null

  LPTowns.findServerTeam = function () {
    try {
      var lpFstIter = Java.loadClass('dev.ftb.mods.ftbteams.api.FTBTeamsAPI')
        .api().getManager().getTeams().iterator()
      var lpFstTeam
      var lpFstType
      while (lpFstIter.hasNext()) {
        lpFstTeam = lpFstIter.next()
        lpFstType = ''
        try { lpFstType = String(lpFstTeam.getType()) } catch (eInner) {}
        if (lpFstType === 'SERVER') return lpFstTeam
      }
    } catch (eOuter) {
      console.warn('[LPTowns] FTB Teams API lookup failed: ' + eOuter)
    }
    return null
  }

  function lpResolveClaimTeamShortName() {
    if (lpCachedClaimTeamShortName) return lpCachedClaimTeamShortName
    var lpRcTeam = LPTowns.findServerTeam()
    if (lpRcTeam) {
      lpCachedClaimTeamShortName = String(lpRcTeam.getShortName())
      console.log('[LPTowns] Resolved claim team: ' + lpCachedClaimTeamShortName)
      return lpCachedClaimTeamShortName
    }
    console.warn('[LPTowns] No SERVER-type team found.')
    return null
  }

  // Biome-id → region tag.
  const COOL_BIOMES = [
    'minecraft:taiga', 'minecraft:snowy_taiga', 'minecraft:snowy_plains',
    'minecraft:snowy_slopes', 'minecraft:grove', 'minecraft:frozen_river',
    'minecraft:old_growth_pine_taiga', 'minecraft:old_growth_spruce_taiga',
    'minecraft:windswept_hills', 'minecraft:windswept_forest',
    'minecraft:windswept_gravelly_hills',
  ]
  const ARID_BIOMES = [
    'minecraft:desert', 'minecraft:badlands', 'minecraft:eroded_badlands',
    'minecraft:wooded_badlands', 'minecraft:savanna', 'minecraft:savanna_plateau',
    'minecraft:windswept_savanna',
  ]
  const TROPICAL_BIOMES = [
    'minecraft:jungle', 'minecraft:bamboo_jungle', 'minecraft:sparse_jungle',
  ]

  function lpRegionForBiome(biomeId) {
    if (COOL_BIOMES.indexOf(biomeId) >= 0) return 'cool'
    if (ARID_BIOMES.indexOf(biomeId) >= 0) return 'arid'
    if (TROPICAL_BIOMES.indexOf(biomeId) >= 0) return 'tropical'
    return 'temperate'
  }

  function lpChunkKey(cx, cz) {
    return cx + '_' + cz
  }

  function lpGetOrCreateRegistry(level) {
    var lpGocData = level.persistentData
    if (!lpGocData.contains(TOWN_DATA_KEY)) {
      lpGocData.put(TOWN_DATA_KEY, {})
    }
    return lpGocData.getCompound(TOWN_DATA_KEY)
  }

  LPTowns.isRegistered = function (level, cx, cz) {
    var lpIsData = level.persistentData
    if (!lpIsData.contains(TOWN_DATA_KEY)) return false
    return lpIsData.getCompound(TOWN_DATA_KEY).contains(lpChunkKey(cx, cz))
  }

  LPTowns.getTown = function (level, cx, cz) {
    var lpGtData = level.persistentData
    if (!lpGtData.contains(TOWN_DATA_KEY)) return null
    var lpGtReg = lpGtData.getCompound(TOWN_DATA_KEY)
    var lpGtKey = lpChunkKey(cx, cz)
    if (!lpGtReg.contains(lpGtKey)) return null
    return lpGtReg.getCompound(lpGtKey)
  }

  LPTowns.registerTown = function (level, blockPos) {
    var lpRtCx = blockPos.x >> 4
    var lpRtCz = blockPos.z >> 4
    var lpRtKey = lpChunkKey(lpRtCx, lpRtCz)

    var lpRtReg = lpGetOrCreateRegistry(level)
    if (lpRtReg.contains(lpRtKey)) return false

    var lpRtName = LPTowns.nameForCoords(lpRtCx, lpRtCz)

    var lpRtBiomeId = 'unknown'
    try {
      lpRtBiomeId = level.getBiome(blockPos).unwrapKey().get().location().toString()
    } catch (eBiome) {
      console.warn('[LPTowns] biome lookup failed at ' + blockPos + ': ' + eBiome)
    }
    var lpRtRegion = lpRegionForBiome(lpRtBiomeId)

    lpRtReg.put(lpRtKey, {
      name: lpRtName,
      region: lpRtRegion,
      biome: lpRtBiomeId,
      cx: lpRtCx,
      cz: lpRtCz,
      bx: blockPos.x,
      by: blockPos.y,
      bz: blockPos.z,
    })

    // Auto-claim around the marker. Anchor is 2D (X Z only) — Y is NOT in
    // the command signature. Tab-completion confirmed: team radius X Z [dim].
    // Passing X Y Z makes Z get parsed as dimension and fails with
    // "Unknown dimension 'minecraft:<z-coord>'".
    var lpRtTeamName = lpResolveClaimTeamShortName() || 'server'
    var lpRtClaimCmd = 'ftbchunks admin claim_as ' + lpRtTeamName + ' '
                    + CLAIM_RADIUS_BLOCKS + ' '
                    + blockPos.x + ' ' + blockPos.z
    var lpRtClaimResult
    try {
      lpRtClaimResult = level.server.runCommandSilent(lpRtClaimCmd)
      console.log('[LPTowns] claim cmd: ' + lpRtClaimCmd
                + ' -> result=' + lpRtClaimResult)
    } catch (eClaim) {
      console.warn('[LPTowns] FTB Chunks claim threw: ' + eClaim)
    }

    // Server-wide announcement
    var lpRtAnnounce = 'tellraw @a ["",'
      + '{"text":"\\u2618 ","color":"gold"},'
      + '{"text":"A new town has been founded: ","color":"gray"},'
      + '{"text":"' + lpRtName + '","color":"yellow","bold":true},'
      + '{"text":" (","color":"gray"},'
      + '{"text":"' + lpRtRegion + '","color":"aqua"},'
      + '{"text":" region)","color":"gray"}]'
    try { level.server.runCommandSilent(lpRtAnnounce) } catch (eAnn) {}

    console.log('[LPTowns] Registered ' + lpRtName + ' (' + lpRtRegion
              + ') at chunk ' + lpRtCx + ',' + lpRtCz
              + ' biome=' + lpRtBiomeId)
    return true
  }
})()

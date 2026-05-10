// One-shot world setup: on first server tick, ensure the FTB Teams server
// team exists and apply all safezone flags. Tracked via a persistent flag
// so it never re-runs and never clobbers user customizations.
//
// Manual user steps still required (NOT automated here):
//   - Edit world/serverconfig/ftbchunks-server.snbt:
//       pvp_mode: "per_team"        (required for allow_pvp to take effect)
//       max_claimed: 50000          (required at scale)
//
// Rhino quirk note: const/let inside any function get hoisted to the
// enclosing IIFE scope and collide on re-invocation. We use only `var`
// with unique prefixed names. forEach-with-arrow is also unsafe because
// the callback runs multiple times within one outer call.

if (typeof LPTowns === 'undefined') var LPTowns = {}

;(function () {
  var LPS_SETUP_DONE_KEY = 'logisticalpack_town_setup_done'
  var LPS_TEAM_DISPLAY_NAME = 'server'

  // Flags applied on first run. Two-element arrays: [key, value-as-string].
  var LPS_FLAGS = [
    ['ftbchunks:allow_pvp', 'false'],
    ['ftbchunks:allow_explosions', 'false'],
    ['ftbchunks:allow_mob_griefing', 'false'],
    ['ftbchunks:allow_fake_players', 'false'],
    ['ftbchunks:block_edit_and_interact_mode', 'private'],
    ['ftbchunks:entity_interact_mode', 'private'],
  ]

  // Use the first ServerEvents.tick instead of ServerEvents.loaded so that
  // a /reload after a failed run will re-attempt setup. ServerEvents.loaded
  // is one-shot per actual server start; /reload reloads scripts but does
  // not re-fire it. The persistent SETUP_DONE_KEY flag still prevents
  // re-run after a successful setup.
  var lpsSetupAttempted = false

  ServerEvents.tick(event => {
    if (lpsSetupAttempted) return
    lpsSetupAttempted = true

    var lpsServer = event.server
    var lpsData = lpsServer.overworld().persistentData

    if (lpsData.contains(LPS_SETUP_DONE_KEY) && lpsData.getBoolean(LPS_SETUP_DONE_KEY)) {
      return
    }

    var lpsTeam = LPTowns.findServerTeam()
    if (!lpsTeam) {
      console.log('[LPTowns/setup] No SERVER team found; creating "' + LPS_TEAM_DISPLAY_NAME + '"')
      try {
        lpsServer.runCommandSilent('ftbteams server create ' + LPS_TEAM_DISPLAY_NAME)
      } catch (e1) {
        console.warn('[LPTowns/setup] Team create failed: ' + e1)
        return
      }
      lpsTeam = LPTowns.findServerTeam()
      if (!lpsTeam) {
        console.warn('[LPTowns/setup] Team create succeeded but no SERVER team '
                   + 'visible afterward — aborting flag setup. Try /reload.')
        return
      }
    } else {
      console.log('[LPTowns/setup] Found existing SERVER team: '
                + String(lpsTeam.getShortName()))
    }

    var lpsShortName = String(lpsTeam.getShortName())

    // Apply flags via plain index loop (no forEach, no const/let)
    var lpsOk = 0
    var lpsFailed = 0
    var lpsI
    var lpsCmd
    for (lpsI = 0; lpsI < LPS_FLAGS.length; lpsI++) {
      lpsCmd = 'ftbteams server settings ' + lpsShortName + ' '
             + LPS_FLAGS[lpsI][0] + ' ' + LPS_FLAGS[lpsI][1]
      try {
        lpsServer.runCommandSilent(lpsCmd)
        lpsOk++
      } catch (e2) {
        console.warn('[LPTowns/setup] ' + LPS_FLAGS[lpsI][0] + '='
                   + LPS_FLAGS[lpsI][1] + ' failed: ' + e2)
        lpsFailed++
      }
    }
    console.log('[LPTowns/setup] Applied ' + lpsOk + ' flags ('
              + lpsFailed + ' failed) to ' + lpsShortName)

    lpsData.putBoolean(LPS_SETUP_DONE_KEY, true)
    console.log('[LPTowns/setup] World setup complete. '
              + 'Edit world/serverconfig/ftbchunks-server.snbt manually for '
              + 'pvp_mode + max_claimed (see WORLD_SETUP.md).')
  })
})()

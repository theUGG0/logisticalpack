# World Setup Checklist

What you have to do by hand on first launch of a new world. The team
creation and safezone flag setup is now **automated** by KubeJS — only
the FTB Chunks server-config edits below are manual.

---

## Automated: FTB Teams + FTB Chunks server-team setup

Handled by `kubejs/server_scripts/towns/town_setup.js` on the first
`ServerEvents.loaded` of every new world. It:

1. Looks for an existing FTB Teams SERVER-type team via the Java API
2. If none, runs `/ftbteams server create server` automatically
3. Applies all 6 safezone flags (allow_pvp/explosions/mob_griefing/
   fake_players off; block_edit_and_interact_mode + entity_interact_mode
   = private)
4. Marks setup done in `level.persistentData.logisticalpack_town_setup_done`
   so it never re-runs (your manual flag overrides survive restarts)

**Verify it ran**: check the server console after world load for
`[LPTowns/setup] World setup complete.`

**Re-run if something went wrong**: clear the persistent flag with
`/data merge entity ... ` (clunky) or use NBT Editor; easier to just
delete the world if it's a fresh setup. To change the default flag
values, edit the `FLAGS` array at the top of `town_setup.js`.

If FTB Teams isn't installed, the script logs a warning and no-ops.

### Manual flag changes (later, ad-hoc)

Use the real short name shown by `/ftbteams list` (it has a
`#xxxxxxxx` UUID-hash suffix, e.g. `server#a1b2c3d4`):

```
/ftbteams server settings server#xxxxxxxx ftbchunks:allow_pvp true
```

Or use the GUI: `/ftbchunks admin open_claim_gui_as server#xxxxxxxx`.

## FTB Chunks — modpack config edits

These are committed to the repo at `config/ftbchunks-world.snbt` and
ship with the pack. No per-world editing needed unless you specifically
want a per-world override (in which case use
`world/serverconfig/ftbchunks-world.snbt` — same filename, different
location, takes precedence).

Already set in this repo:

```snbt
pvp_mode: "per_team"           # required for team-level allow_pvp to work
max_claimed_chunks: 50000      # bumped from default 500; each town claims ~289
```

If you ever need to bump the value further, edit
`config/ftbchunks-world.snbt` in the repo and restart the server.

---

## Trade-town pipeline sanity test (admin / dev)

Verify the auto-naming + auto-claim works end-to-end. In creative:

```
/give @s logisticalpack:town_center
```

Place the block on the ground. Walk **out** of that chunk and back **in**.
Within ~1 second expect:

- Chat: `☘ A new town has been founded: <Name> (<region> region)`
- Server log: `[LPTowns] Registered <Name> (<region>) at chunk X,Z biome=<id>`
- FTB Chunks map (M key by default): chunks within 128 blocks of the marker
  claimed by the `server` team.

If nothing happens, check the server log for `[LPTowns]` errors and verify
`/ftbchunks admin claim_as server 128 ~ ~ ~` works manually as op.

---

## When this list grows

Append new sections as features land. Each section should answer:
- **What it sets up** (one sentence)
- **The exact commands or file edits** (copy-pasteable)
- **How to verify it worked** (one quick check)

# NPC preset generator

Programmatic builder for the Easy NPC `.npc.nbt` preset files in
`kubejs/data/easy_npc/preset/humanoid/`. The dispatcher
(`/lp_setup_npc <type> <region>`, see
`kubejs/server_scripts/towns/npcs/dispatcher.js`) spawns NPCs from
those files.

## Run

```
pip install --user nbtlib            # one-time
python3 dev-mods/preset-generator/build_presets.py
```

Output: 24 preset files (6 NPC types × 4 regions).

## Editing

`build_presets.py` is the single source of truth for trade data,
prices, and skin variants. Each NPC type lives in the `NPC_TYPES`
dict near the top — add a new type by adding a new entry, then add
its name to `LPNpc_TYPES` in
`kubejs/server_scripts/towns/npcs/dispatcher.js`.

Region multipliers live in `REGION_MULTIPLIERS`. Categories are
arbitrary strings — use the existing ones for consistency, add new
ones if a type needs different regional behavior than what's already
defined.

## Important

The generator OVERWRITES existing `.npc.nbt` files. Hand-tweaks via
the Easy NPC wand UI to **preset files** are wiped on re-run. This is
intentional: the .npc.nbt files are build artifacts. Customize NPCs
by editing them after spawn, not by editing the presets.

## Template dependency

The script needs an existing `innkeeper_temperate.npc.nbt` to seed
the entity-side defaults (Brain, EntityAttribute, Navigation, etc.)
that don't change between presets. If that file is ever lost,
regenerate it by configuring an Innkeeper via wand UI, save+quit,
and copy `saves/<world>/easy_npc/npcs/<UUID>.npc.nbt` over the
preset path. Then re-run this script.

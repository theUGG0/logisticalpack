# Innkeeper Template Setup (Temperate Region)

Walks through configuring the **temperate** Innkeeper template once via the
Easy NPC GUI, then exporting it as a preset that ships with the modpack.
Repeat this procedure for `cool` / `arid` / `tropical` after the first one
works (prices change per region — see `kubejs/server_scripts/towns/npcs/prices.js`).

## 0. Pre-flight

You need OP / creative. Fly somewhere boring (not in a registered town —
we don't want the test NPC permanently in a real town).

Give yourself the wand and a spawn egg:
```
/give @s easy_npc:easy_npc_wand
/give @s easy_npc:humanoid_spawn_egg
```

## 1. Spawn and identify the NPC

1. Right-click ground with the spawn egg. A default humanoid appears.
2. Right-click the NPC with the wand. Configuration GUI opens.
3. Note the NPC's UUID (top of GUI or `/data get entity @e[type=easy_npc:humanoid,limit=1,sort=nearest] UUID`).

## 2. Set name and appearance

- **Name** tab: `Innkeeper` (this is what shows above the NPC's head).
- **Skin** tab: pick anything appropriate — a villager-like or commoner skin.
- **Profession** tab (if available): `BUTCHER` or `FARMER` are thematic.
- **Dialog** tab (optional for now): leave blank or set a one-line greeting
  like `Welcome, traveler. Care for a meal?`

## 3. Configure trades

Navigate **Trading → Basic** in the wand GUI, then **Add new trade** for
each row below. For each trade:

- **Input slot A**: drop in the listed coin item × count
- **Input slot B**: leave empty
- **Output slot**: drop in the listed item × count
- **Max uses** (stock per restock): set to the value in the table

Coin items (Numismatics):
- `numismatics:spur` — base unit
- `numismatics:bevel` — 8 spurs
- `numismatics:sprocket` — 64 spurs

### Trade table — temperate region

All prices below are baseline (1.0× multiplier). Cool/arid/tropical variants
use the same items but with prices computed by `LPNpc.priceFor(entry, region)`.

| # | Tier | Input | Output | Max uses |
|---|---|---|---|---|
| 1 | Trash | 2 × `numismatics:spur` | 1 × `minecraft:rotten_flesh` | 32 |
| 2 | Trash | 2 × `numismatics:spur` | 1 × `minecraft:chicken` (raw) | 32 |
| 3 | Trash | 4 × `numismatics:spur` | 1 × `minecraft:cod` (raw) | 32 |
| 4 | Daily | 4 × `numismatics:spur` | 1 × `minecraft:bread` | 16 |
| 5 | Daily | 4 × `numismatics:spur` | 1 × `minecraft:carrot` | 16 |
| 6 | Daily | 1 × `numismatics:bevel` | 1 × `minecraft:apple` | 16 |
| 7 | Daily | 1 × `numismatics:bevel` | 1 × `minecraft:baked_potato` | 16 |
| 8 | Cooked | 2 × `numismatics:bevel` | 1 × `minecraft:cooked_chicken` | 12 |
| 9 | Cooked | 2 × `numismatics:bevel` | 1 × `minecraft:cooked_beef` | 12 |
| 10 | Cooked | 1 × `numismatics:bevel` | 1 × `minecraft:cooked_cod` | 12 |
| 11 | Premium | 3 × `numismatics:bevel` | 1 × `minecraft:mushroom_stew` | 6 |
| 12 | Premium | 4 × `numismatics:bevel` | 1 × `minecraft:cooked_salmon` | 6 |
| 13 | Premium | 5 × `numismatics:bevel` | 1 × `minecraft:rabbit_stew` | 6 |
| 14 | Crop simple | 4 × `numismatics:bevel` | 1 × `croptopia:tomato` | 4 |
| 15 | Crop simple | 5 × `numismatics:bevel` | 1 × `croptopia:strawberry` | 4 |
| 16 | Crop simple | 6 × `numismatics:bevel` | 1 × `croptopia:rice` | 4 |
| 17 | Crop gourmet | 1 × `numismatics:sprocket` | 1 × `croptopia:caesar_salad` | 2 |
| 18 | Crop gourmet | 2 × `numismatics:sprocket` | 1 × `croptopia:cheese_pizza` | 2 |

**Croptopia gourmet item ids may differ in your version — verify in JEI
before adding trade #17/#18. If the id is different, swap to whatever
gourmet dish exists.** Update prices.js to match if you change items.

Save / close the GUI when done.

## 4. Sanity-check the configured NPC

Without the wand in your hand, right-click the NPC. The trading screen
should open with all 18 trades listed. Spend a few coins to verify a
trade or two completes. Optionally:
- `/give @s numismatics:bevel 8`
- Try trade #6: 1 bevel → 1 apple

## 5. Save and Quit, then copy the persisted NPC file

**This step is non-negotiable.** Easy NPC saves trade data lazily — the
.npc.nbt file does NOT contain trades until the entity is unloaded via a
proper Save and Quit to Title. Autosave is not enough. `/save-all` is not
enough. You must return to the title screen.

1. **Save and Quit to Title** in the pause menu.
2. The persisted file lives at:
   ```
   saves/<World Name>/easy_npc/npcs/<UUID>.npc.nbt
   ```
   It will be ~1900+ bytes (vs ~1500 bytes when trades aren't saved).
3. Copy it into the datapack:
   ```
   cp saves/<World>/easy_npc/npcs/<UUID>.npc.nbt \
      kubejs/data/easy_npc/preset/humanoid/innkeeper_temperate.npc.nbt
   ```

The `/easy_npc preset export` command's chat message claims a `config/`
path but in practice does not write there. Use the world-save file
above as the authoritative artifact. (See
`memory/easy_npc_export_save_quit.md` for full context.)

The preset is now loadable by:

```
/easy_npc preset import_new data easy_npc:preset/humanoid/innkeeper_temperate.npc.nbt
```

## 6. Verify the preset reimports cleanly

In a fresh location:
```
/easy_npc preset import_new data easy_npc:preset/humanoid/innkeeper_temperate.npc.nbt
```

Should spawn a new Innkeeper NPC with all 18 trades intact. Right-click
to confirm.

## 7. Repeat for the other 3 regions

Same procedure, but use prices computed by region. Quick reference
(LPNpc.priceFor multipliers, applied per category):

| Category | temperate | cool | arid | tropical |
|---|---|---|---|---|
| meat | 1.0× | 0.8× | 1.4× | 1.2× |
| fish | 1.0× | 0.8× | 1.6× | 0.9× |
| grain | 1.0× | 1.3× | 0.7× | 1.3× |
| fruit | 1.0× | 1.5× | 1.2× | 0.6× |
| veg | 1.0× | 1.2× | 1.5× | 0.9× |
| gourmet | 1.0× | 1.0× | 1.0× | 1.0× |

Once the dispatcher is built (`/lp_setup_npc innkeeper`), it auto-picks the
right preset based on the town you're standing in.

---

## Why this is manual (and not scripted)

Easy NPC's `/easy_npc trading` chat command only exposes `open` and `reset`
— no `add`/`set`. Trade configuration must go through the GUI. The preset
system (export → datapack → import_new) is the version-controllable
substitute. See `~/.claude/.../memory/easy_npc_no_trade_scripting.md`
for the full constraint.

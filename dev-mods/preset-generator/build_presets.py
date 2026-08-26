#!/usr/bin/env python3
"""
LogisticalPack NPC preset generator.

Reads a base template .npc.nbt and emits a fresh preset for every
(npc_type, region) combination, writing them into the modpack's KubeJS
datapack. The dispatcher (`/lp_setup_npc <type> <region>`) then spawns
NPCs from these presets.

Source-of-truth for prices/items lives in this file. Re-run after edits:

    python3 dev-mods/preset-generator/build_presets.py

Output goes to:
    kubejs/data/easy_npc/preset/humanoid/<type>_<region>.npc.nbt

Requires: nbtlib (pip install --user nbtlib).

Notes
-----
* The generator OVERWRITES existing .npc.nbt files. Hand-tweaks made via
  the Easy NPC wand UI (skin URLs, dialog content, custom actions) will
  be lost on re-run. For one-off customizations, edit per-type defaults
  here and re-run. For real personalization, do it via the wand on the
  spawned NPC, not on the preset.
* Skin variants use Easy NPC's built-in humanoid models (no custom PNG
  required). Swap the `variant` field per type if you don't like the
  assignment.
* TradingDataSet.Type is forced to BASIC so the LP fork's load-time
  fix has nothing to do — these presets are pre-correct.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    from nbtlib import (
        File, Compound, List, String, Int, Long, Float, Byte, IntArray
    )
except ImportError:
    print("ERROR: nbtlib not installed. Run: pip install --user nbtlib")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRESET_DIR = REPO_ROOT / "kubejs" / "data" / "easy_npc" / "preset" / "humanoid"
TEMPLATE_PATH = PRESET_DIR / "innkeeper_temperate.npc.nbt"


# ---------------------------------------------------------------------------
# Region multipliers
# ---------------------------------------------------------------------------
# Categories used by the type tables below. Add new ones as needed; any
# category not listed here defaults to 1.0× (region-neutral).
REGION_MULTIPLIERS = {
    "temperate": {
        "meat": 1.0, "fish": 1.0, "grain": 1.0, "fruit": 1.0, "veg": 1.0,
        "gourmet": 1.0, "trash": 1.0, "wood": 1.0, "mineral": 1.0,
        "premium": 1.0, "common": 1.0, "info": 1.0, "light": 1.0,
        "processed": 1.0,
    },
    "cool": {
        "meat": 0.8, "fish": 0.8, "grain": 1.3, "fruit": 1.5, "veg": 1.2,
        "gourmet": 1.0, "trash": 1.0, "wood": 0.9, "mineral": 1.0,
        "premium": 1.0, "common": 1.0, "info": 1.0, "light": 1.0,
        "processed": 1.0,
    },
    "arid": {
        "meat": 1.4, "fish": 1.6, "grain": 0.7, "fruit": 1.2, "veg": 1.5,
        "gourmet": 1.0, "trash": 1.0, "wood": 1.4, "mineral": 0.8,
        "premium": 1.0, "common": 1.0, "info": 1.0, "light": 1.0,
        "processed": 1.1,
    },
    "tropical": {
        "meat": 1.2, "fish": 0.9, "grain": 1.3, "fruit": 0.6, "veg": 0.9,
        "gourmet": 1.0, "trash": 1.0, "wood": 0.7, "mineral": 1.0,
        "premium": 1.0, "common": 1.0, "info": 1.0, "light": 1.0,
        "processed": 1.0,
    },
}


# ---------------------------------------------------------------------------
# NPC type definitions
# ---------------------------------------------------------------------------
# Tuple format per item:
#   (sell_id, sell_count, base_spurs, max_uses, category)
#
# Prices in spurs (Numismatics base unit). The generator picks the
# largest clean denomination at format time (8=bevel, 64=sprocket,
# 512=cog, 4096=crown). Avoid awkward values that don't round cleanly:
# stick to multiples of 4/8/16/32/64 above 8 spur for clean stacks.
#
# Skin variants are Easy NPC built-ins for the humanoid model:
# STEVE, ALEX, ARI, EFE, KAI, MAKENA, NOOR, SUNNY, ZURI, JAYJASONBO.

NPC_TYPES = {
    # ----- Innkeeper: food vendor (vanilla + Croptopia) ---------------
    "innkeeper": {
        "display_name": "Innkeeper",
        "variant": "ALEX",
        "items": [
            # Trash tier — cleanup buys
            ("minecraft:rotten_flesh",     1,  2, 32, "trash"),
            ("minecraft:chicken",          1,  2, 32, "meat"),    # raw, food poisoning risk
            ("minecraft:cod",              1,  4, 32, "fish"),    # raw
            # Daily staples
            ("minecraft:bread",            1,  4, 16, "grain"),
            ("minecraft:carrot",           1,  4, 16, "veg"),
            ("minecraft:apple",            1,  8, 16, "fruit"),
            # Cooked basics
            ("minecraft:cooked_chicken",   1, 16, 12, "meat"),
            ("minecraft:cooked_beef",      1, 16, 12, "meat"),
            ("minecraft:cooked_cod",       1,  8, 12, "fish"),
            # Premium dishes
            ("minecraft:mushroom_stew",    1, 24,  6, "veg"),
            ("minecraft:cooked_salmon",    1, 32,  6, "fish"),
            ("minecraft:rabbit_stew",      1, 40,  6, "meat"),
            # Croptopia simple (raw)
            ("croptopia:tomato",           1, 32,  4, "veg"),
            ("croptopia:strawberry",       1, 40,  4, "fruit"),
            ("croptopia:rice",             1, 48,  4, "grain"),
            # Croptopia gourmet (cooked dishes; flat across regions)
            ("croptopia:caesar_salad",     1, 64,  2, "gourmet"),
            ("croptopia:cheese_pizza",     1,128,  2, "gourmet"),
        ],
    },

    # ----- Town Crier: information broker --------------------------------
    "town_crier": {
        "display_name": "Town Crier",
        "variant": "NOOR",
        "items": [
            ("minecraft:paper",                 4,  4, 32, "info"),
            ("minecraft:book",                  1,  8, 16, "info"),
            ("minecraft:writable_book",         1, 16, 12, "info"),
            ("minecraft:compass",               1, 16,  8, "info"),
            ("minecraft:recovery_compass",      1, 64,  4, "premium"),
            ("minecraft:bell",                  1, 32,  6, "info"),
            ("minecraft:lodestone",             1,128,  2, "premium"),
            ("minecraft:experience_bottle",     1, 24,  8, "info"),
            ("minecraft:bundle",                1,  8, 16, "common"),
            ("minecraft:lantern",               1,  8, 12, "light"),
        ],
    },

    # ----- General Trader: town staples ---------------------------------
    "general_trader": {
        "display_name": "General Trader",
        "variant": "SUNNY",
        "items": [
            ("minecraft:torch",                 8,  4, 32, "light"),
            ("minecraft:lantern",               1,  8, 12, "light"),
            ("minecraft:oak_planks",            8,  4, 24, "wood"),
            ("minecraft:cobblestone",          16,  4, 24, "common"),
            ("minecraft:white_wool",            1,  8, 16, "common"),
            ("minecraft:string",                4,  8, 16, "common"),
            ("minecraft:leather",               1, 16, 12, "common"),
            ("minecraft:flint_and_steel",       1,  8,  8, "common"),
            ("minecraft:bucket",                1,  8,  8, "common"),
            ("minecraft:water_bucket",          1, 16,  4, "premium"),
            ("minecraft:saddle",                1, 32,  2, "premium"),
            ("minecraft:name_tag",              1, 16,  4, "premium"),
            ("minecraft:bone_meal",             8,  4, 24, "common"),
            ("minecraft:glass",                16, 16, 12, "processed"),
        ],
    },

    # ----- Prospector: mining gear --------------------------------------
    "prospector": {
        "display_name": "Prospector",
        "variant": "KAI",
        "items": [
            ("minecraft:wooden_pickaxe",        1,  8, 12, "common"),
            ("minecraft:stone_pickaxe",         1, 16, 12, "common"),
            ("minecraft:iron_pickaxe",          1, 64,  6, "mineral"),
            ("minecraft:diamond_pickaxe",       1,256,  2, "premium"),
            ("minecraft:torch",                16, 16, 16, "light"),
            ("minecraft:flint",                 4, 16, 12, "common"),
            ("minecraft:cobblestone",          32,  8, 16, "common"),
            ("minecraft:lodestone",             1,128,  2, "premium"),
            ("minecraft:experience_bottle",     1, 24,  8, "info"),
            ("minecraft:tnt",                   1, 64,  4, "premium"),
            ("minecraft:gunpowder",             4, 16,  8, "common"),
        ],
    },

    # ----- Ore Trader: raw resources at premium prices ------------------
    "ore_trader": {
        "display_name": "Ore Trader",
        "variant": "EFE",
        "items": [
            ("minecraft:coal",                  4, 16, 16, "mineral"),
            ("minecraft:iron_ingot",            1, 32, 12, "mineral"),
            ("minecraft:copper_ingot",          1, 32, 12, "mineral"),
            ("minecraft:gold_ingot",            1, 64,  8, "mineral"),
            ("minecraft:redstone",              4, 32, 16, "mineral"),
            ("minecraft:lapis_lazuli",          4, 32, 16, "mineral"),
            ("minecraft:quartz",                4, 32, 12, "mineral"),
            ("minecraft:amethyst_shard",        4, 32,  8, "mineral"),
            ("minecraft:diamond",               1,128,  4, "premium"),
            ("minecraft:emerald",               1,192,  4, "premium"),
            ("minecraft:netherite_scrap",       1,512,  2, "premium"),
            ("minecraft:flint",                 4, 16, 16, "common"),
        ],
    },

    # ----- Goods Merchant: processed/decorative goods + Create items ---
    "goods_merchant": {
        "display_name": "Goods Merchant",
        "variant": "ARI",
        "items": [
            ("minecraft:bricks",                4, 32, 12, "processed"),
            ("minecraft:nether_bricks",         4, 24, 12, "processed"),
            ("minecraft:terracotta",            4, 16, 16, "processed"),
            ("minecraft:white_glazed_terracotta", 1, 32, 8, "premium"),
            ("minecraft:glass_pane",            4, 16, 16, "processed"),
            ("minecraft:tinted_glass",          1, 64,  4, "premium"),
            ("minecraft:white_dye",             4,  8, 24, "common"),
            ("minecraft:black_dye",             4,  8, 24, "common"),
            ("minecraft:red_dye",               4,  8, 24, "common"),
            ("minecraft:blue_dye",              4,  8, 24, "common"),
            ("create:andesite_casing",          1, 24, 12, "processed"),
            ("create:brass_casing",             1, 32,  8, "processed"),
            ("create:zinc_ingot",               1, 16, 12, "mineral"),
            ("create:brass_ingot",              1, 32, 12, "mineral"),
            ("create:copper_sheet",             1, 16, 12, "mineral"),
        ],
    },
}


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def nice_round(spurs: float) -> int:
    """Round to the nearest clean denomination boundary.

    Mirrors `lpNiceRound` in prices.js so our generated presets snap
    to the same values an admin would derive by hand.
    """
    if spurs <= 0:
        return 1
    if spurs < 8:
        return max(1, round(spurs))
    if spurs < 64:
        return max(8, round(spurs / 8) * 8)
    return max(64, round(spurs / 64) * 64)


def format_price_input(spurs: int) -> tuple[str, int]:
    """Pick the largest single-stack denomination for `spurs` spur-equivalent."""
    if spurs >= 4096 and spurs % 4096 == 0:
        return ("numismatics:crown",    spurs // 4096)
    if spurs >=  512 and spurs %  512 == 0:
        return ("numismatics:cog",      spurs //  512)
    if spurs >=   64 and spurs %   64 == 0:
        return ("numismatics:sprocket", spurs //   64)
    if spurs >=    8 and spurs %    8 == 0:
        return ("numismatics:bevel",    spurs //    8)
    return ("numismatics:spur", spurs)


def build_recipe(item_id: str, item_count: int, base_spurs: int,
                 max_uses: int, category: str, region: str) -> Compound:
    """Build a single MerchantOffer Recipe compound."""
    mult = REGION_MULTIPLIERS[region].get(category, 1.0)
    final_spurs = nice_round(base_spurs * mult)
    coin_id, coin_count = format_price_input(final_spurs)
    return Compound({
        "buy":             Compound({"id": String(coin_id), "count": Int(coin_count)}),
        "sell":            Compound({"id": String(item_id), "count": Int(item_count)}),
        "maxUses":         Int(max_uses),
        "xp":              Int(0),
        "priceMultiplier": Float(1.0),
    })


def build_offers(items: list, region: str) -> Compound:
    return Compound({"Recipes": List([build_recipe(*item, region) for item in items])})


# ---------------------------------------------------------------------------
# Preset generation
# ---------------------------------------------------------------------------

def make_preset(template: File, npc_type_data: dict, region: str,
                out_path: Path) -> None:
    # Deep-clone the template by re-reading the bytes — nbtlib doesn't
    # expose a cheap deep-copy, so this is the safest way to get an
    # independent tag tree per output.
    file = File.load(TEMPLATE_PATH, gzipped=True)

    # 1. Replace Offers/Recipes with our generated trade list.
    file["Offers"] = build_offers(npc_type_data["items"], region)

    # 2. Force TradingDataSet.Type = BASIC. The LP fork would set this
    #    on first load, but baking it in keeps the preset self-consistent.
    if "TradingData" in file and "TradingDataSet" in file["TradingData"]:
        file["TradingData"]["TradingDataSet"]["Type"] = String("BASIC")

    # 3. Skin variant.
    file["VariantType"] = String(npc_type_data["variant"])
    if "SkinData" in file:
        file["SkinData"]["Name"] = String(npc_type_data["variant"])
        file["SkinData"]["Type"] = String("DEFAULT")
        file["SkinData"]["Content"] = String("")
        file["SkinData"]["URL"] = String("")
        file["SkinData"]["UUID"] = IntArray([0, 0, 0, 0])

    # 4. Display name (CustomName, JSON component string).
    display = f"{npc_type_data['display_name']} ({region.capitalize()})"
    file["CustomName"] = String('{"text":"' + display + '"}')
    file["CustomNameVisible"] = Byte(1)

    # 5. Preset metadata.
    if "PresetMetadata" in file:
        meta = file["PresetMetadata"]
        meta["name"] = String(display)
        meta["description"] = String(
            f"LogisticalPack {npc_type_data['display_name']}, {region} region")
        meta["category"] = String("LogisticalPack")
        meta["author"] = String("LogisticalPack")
        now_ms = Long(int(time.time() * 1000))
        meta["modified"] = now_ms
        meta["created"] = now_ms

    # 6. Save (gzipped per Easy NPC convention).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(out_path, gzipped=True)


def main() -> int:
    if not TEMPLATE_PATH.exists():
        print(f"ERROR: template not found at {TEMPLATE_PATH}")
        print("       Configure one Innkeeper via the wand UI first")
        print("       (see INNKEEPER_TEMPLATE_SETUP.md), save+quit, copy")
        print("       the .npc.nbt file there.")
        return 1

    template = File.load(TEMPLATE_PATH, gzipped=True)
    print(f"Template: {TEMPLATE_PATH.name} ({TEMPLATE_PATH.stat().st_size} bytes)")
    print(f"Output:   {PRESET_DIR}")
    print()

    total = 0
    for npc_type, data in NPC_TYPES.items():
        for region in REGION_MULTIPLIERS.keys():
            out = PRESET_DIR / f"{npc_type}_{region}.npc.nbt"
            make_preset(template, data, region, out)
            n_items = len(data["items"])
            print(f"  {npc_type}_{region}.npc.nbt  ({n_items} trades, variant={data['variant']})")
            total += 1

    print()
    print(f"Generated {total} preset files "
          f"({len(NPC_TYPES)} types × {len(REGION_MULTIPLIERS)} regions).")
    print()
    print("Next: in game, /reload then test:")
    for npc_type in NPC_TYPES.keys():
        print(f"  /lp_setup_npc {npc_type} temperate")
    return 0


if __name__ == "__main__":
    sys.exit(main())

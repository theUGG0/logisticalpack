// Source-of-truth for all NPC trade prices. Easy NPC trades cannot be
// scripted via /easy_npc trading commands (only `open` / `reset` exist),
// so this file does NOT push trades onto NPCs at runtime — instead it is
// the canonical reference an admin reads while configuring template NPCs
// in the Easy NPC GUI. The LPNpc.* helpers below also let future code
// (dispatcher, audit checker, NBT writer) compute the same numbers.
//
// Pricing model:
//   1. Each item entry has a baseSpur cost and a category tag.
//   2. Region multipliers (REGION_MULTIPLIERS[region][category]) scale
//      the base by local supply/demand.
//   3. Final price (in spurs) is rounded to the nearest "nice" value
//      so it maps to a clean denomination stack the operator can enter.
//
// Numismatics denominations (powers of 8):
//   spur=1, bevel=8, sprocket=64, cog=512, crown=4096, sun=32768

if (typeof LPNpc === 'undefined') var LPNpc = {}

;(function () {
  // ---- Region multipliers -------------------------------------------------
  // Each region overrides per-category cost. 1.0 = baseline.
  // Categories: meat, fish, grain, fruit, veg, gourmet, trash.
  // Trash and gourmet are flat across regions (rotten flesh tastes the same
  // everywhere; gourmet dishes are luxury items priced by recipe complexity).
  LPNpc.REGION_MULTIPLIERS = {
    temperate: { meat: 1.0, fish: 1.0, grain: 1.0, fruit: 1.0, veg: 1.0, gourmet: 1.0, trash: 1.0 },
    cool:      { meat: 0.8, fish: 0.8, grain: 1.3, fruit: 1.5, veg: 1.2, gourmet: 1.0, trash: 1.0 },
    arid:      { meat: 1.4, fish: 1.6, grain: 0.7, fruit: 1.2, veg: 1.5, gourmet: 1.0, trash: 1.0 },
    tropical:  { meat: 1.2, fish: 0.9, grain: 1.3, fruit: 0.6, veg: 0.9, gourmet: 1.0, trash: 1.0 },
  }

  // ---- Innkeeper trade list ----------------------------------------------
  // Each entry: { item, count, baseSpur, maxUses, category }
  //   - item:     output item id
  //   - count:    output count per trade
  //   - baseSpur: cost in spurs at baseline (temperate) region
  //   - maxUses:  Easy NPC stock per restock cycle
  //   - category: looked up in REGION_MULTIPLIERS
  //
  // Croptopia gourmet item ids are best-guess from current Croptopia
  // (verify in JEI before configuring; ids commented as VERIFY).
  LPNpc.PRICES_INNKEEPER = [
    // --- Trash tier (cleanup buys; always available, low value) ---
    { item: 'minecraft:rotten_flesh',  count: 1, baseSpur:  2, maxUses: 32, category: 'trash' },
    { item: 'minecraft:dried_kelp',    count: 1, baseSpur:  1, maxUses: 32, category: 'trash' },
    { item: 'minecraft:chicken',       count: 1, baseSpur:  2, maxUses: 32, category: 'meat'  }, // raw, food-poisoning risk
    { item: 'minecraft:cod',           count: 1, baseSpur:  4, maxUses: 32, category: 'fish'  }, // raw
    { item: 'minecraft:potato',        count: 1, baseSpur:  2, maxUses: 32, category: 'veg'   }, // raw

    // --- Daily staples (4-8 spur) ---
    { item: 'minecraft:bread',         count: 1, baseSpur:  4, maxUses: 16, category: 'grain' },
    { item: 'minecraft:carrot',        count: 1, baseSpur:  4, maxUses: 16, category: 'veg'   },
    { item: 'minecraft:apple',         count: 1, baseSpur:  8, maxUses: 16, category: 'fruit' },
    { item: 'minecraft:baked_potato',  count: 1, baseSpur:  8, maxUses: 16, category: 'veg'   },
    { item: 'minecraft:wheat',         count: 2, baseSpur:  8, maxUses: 16, category: 'grain' },
    { item: 'minecraft:beetroot',      count: 1, baseSpur:  4, maxUses: 16, category: 'veg'   },

    // --- Cooked basic (8-24 spur, 1-3 bevel) ---
    { item: 'minecraft:cooked_chicken',   count: 1, baseSpur: 16, maxUses: 12, category: 'meat' },
    { item: 'minecraft:cooked_beef',      count: 1, baseSpur: 16, maxUses: 12, category: 'meat' },
    { item: 'minecraft:cooked_porkchop',  count: 1, baseSpur: 16, maxUses: 12, category: 'meat' },
    { item: 'minecraft:cooked_cod',       count: 1, baseSpur:  8, maxUses: 12, category: 'fish' },
    { item: 'minecraft:cooked_mutton',    count: 1, baseSpur: 24, maxUses: 12, category: 'meat' },

    // --- Premium dishes (24-40 spur, 3-5 bevel) ---
    { item: 'minecraft:mushroom_stew',    count: 1, baseSpur: 24, maxUses:  6, category: 'veg'    },
    { item: 'minecraft:beetroot_soup',    count: 1, baseSpur: 24, maxUses:  6, category: 'veg'    },
    { item: 'minecraft:cooked_salmon',    count: 1, baseSpur: 32, maxUses:  6, category: 'fish'   },
    { item: 'minecraft:rabbit_stew',      count: 1, baseSpur: 40, maxUses:  6, category: 'meat'   },
    { item: 'minecraft:pumpkin_pie',      count: 1, baseSpur: 32, maxUses:  6, category: 'gourmet' },

    // --- Croptopia simple (raw fruits/veg, 32-48 spur, 4-6 bevel) ---
    // Encourages players to grow their own; resale is unprofitable.
    { item: 'croptopia:tomato',           count: 1, baseSpur: 32, maxUses:  4, category: 'veg'   },
    { item: 'croptopia:lemon',            count: 1, baseSpur: 32, maxUses:  4, category: 'fruit' },
    { item: 'croptopia:strawberry',       count: 1, baseSpur: 40, maxUses:  4, category: 'fruit' },
    { item: 'croptopia:lettuce',          count: 1, baseSpur: 40, maxUses:  4, category: 'veg'   },
    { item: 'croptopia:rice',             count: 1, baseSpur: 48, maxUses:  4, category: 'grain' },
    { item: 'croptopia:onion',            count: 1, baseSpur: 32, maxUses:  4, category: 'veg'   },

    // --- Croptopia gourmet (cooked dishes, 1-3 sprocket = 64-192 spur) ---
    // Flat-priced across regions (gourmet category); rare stock.
    { item: 'croptopia:caesar_salad',     count: 1, baseSpur:  64, maxUses: 2, category: 'gourmet' }, // VERIFY id
    { item: 'croptopia:fruit_salad',      count: 1, baseSpur:  64, maxUses: 2, category: 'gourmet' }, // VERIFY id
    { item: 'croptopia:cheese_pizza',     count: 1, baseSpur: 128, maxUses: 2, category: 'gourmet' }, // VERIFY id
    { item: 'croptopia:apple_pie',        count: 1, baseSpur: 192, maxUses: 2, category: 'gourmet' }, // VERIFY id
  ]

  // ---- Helpers ------------------------------------------------------------
  // Round to nearest "nice" denomination boundary so the price maps cleanly
  // to a single-stack input. Small (<8) prices stay as raw spur; medium
  // (>=8) snap to a multiple of 8 so they map to whole bevels; >=64 snap to
  // multiples of 64 so they map to whole sprockets. Result is then re-encoded
  // by formatPrice into the largest single denomination ≤64 stack size.
  function lpNiceRound(spurs) {
    if (spurs <= 0) return 1
    if (spurs < 8)  return Math.max(1, Math.round(spurs))
    if (spurs < 64) return Math.max(8, Math.round(spurs / 8) * 8)
    return Math.max(64, Math.round(spurs / 64) * 64)
  }

  // Compute final price for an entry in a given region.
  // Returns the spur-equivalent integer cost.
  LPNpc.priceFor = function (entry, region) {
    var lpPfMult = (LPNpc.REGION_MULTIPLIERS[region] || LPNpc.REGION_MULTIPLIERS.temperate)[entry.category]
    if (typeof lpPfMult !== 'number') lpPfMult = 1.0
    return lpNiceRound(entry.baseSpur * lpPfMult)
  }

  // Convert a spur-equivalent integer into the largest single denomination
  // that fits cleanly. Returns { item, count } for direct GUI entry.
  // If the number doesn't divide cleanly into a higher denomination, falls
  // back to spurs (so 12 spur stays 12 spur, not "1 bevel + 4 spur").
  LPNpc.formatPrice = function (spurs) {
    if (spurs >= 4096 && spurs % 4096 === 0) return { item: 'numismatics:crown',    count: spurs / 4096 }
    if (spurs >=  512 && spurs %  512 === 0) return { item: 'numismatics:cog',      count: spurs /  512 }
    if (spurs >=   64 && spurs %   64 === 0) return { item: 'numismatics:sprocket', count: spurs /   64 }
    if (spurs >=    8 && spurs %    8 === 0) return { item: 'numismatics:bevel',    count: spurs /    8 }
    return { item: 'numismatics:spur', count: spurs }
  }

  // Convenience: full trade tuple for a given entry+region.
  // Returns { input: {item,count}, output: {item,count}, maxUses }.
  // This is what a future dispatcher / NBT writer would consume.
  LPNpc.tradeFor = function (entry, region) {
    return {
      input:   LPNpc.formatPrice(LPNpc.priceFor(entry, region)),
      output:  { item: entry.item, count: entry.count },
      maxUses: entry.maxUses,
    }
  }

  // Dump full innkeeper trade table for a region as an array. Useful for
  // generating setup checklists or audit-comparing a configured NPC.
  LPNpc.innkeeperTradesForRegion = function (region) {
    var lpItrOut = []
    var lpItrEntry
    for (var lpItrI = 0; lpItrI < LPNpc.PRICES_INNKEEPER.length; lpItrI++) {
      lpItrEntry = LPNpc.PRICES_INNKEEPER[lpItrI]
      lpItrOut.push(LPNpc.tradeFor(lpItrEntry, region))
    }
    return lpItrOut
  }
})()

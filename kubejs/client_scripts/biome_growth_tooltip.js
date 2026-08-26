// =============================================================================
// Croptopia seed tooltips:
//   1. modifyTooltips — register static climate line + dynamic placeholder
//   2. dynamicTooltips — fill placeholder with the live "Growing in [Biome]" line
// =============================================================================

const Minecraft = Java.loadClass('net.minecraft.client.Minecraft')

// Compare biome tags via biomeHolder.tags() iteration — Rhino can't resolve
// Holder.is(...) overloads with TagKey, and kjs$isTag isn't visible.
function biomeTagSet(biomeHolder) {
  const out = {}
  const iter = biomeHolder.tags().iterator()
  while (iter.hasNext()) {
    out[iter.next().location().toString()] = true
  }
  return out
}

function biomeHasAny(biomeTags, candidates) {
  for (let i = 0; i < candidates.length; i++) {
    if (biomeTags[candidates[i]]) return true
  }
  return false
}

const CROP_BUCKETS = {
  pineapple: 'tropical', coffee_beans: 'tropical', vanilla: 'tropical',
  ginger: 'tropical', turmeric: 'tropical', tea_leaves: 'tropical',
  chile_pepper: 'tropical', pepper: 'tropical', yam: 'tropical',
  sweetpotato: 'tropical', peanut: 'tropical', basil: 'tropical',
  bellpepper: 'tropical',

  // Temperate = warm-season crops; suffer in frost.
  tomato: 'temperate', tomatillo: 'temperate', corn: 'temperate',
  cucumber: 'temperate', squash: 'temperate', zucchini: 'temperate',
  eggplant: 'temperate', soybean: 'temperate', blackbean: 'temperate',
  greenbean: 'temperate', cantaloupe: 'temperate', honeydew: 'temperate',
  grape: 'temperate', olive: 'temperate', artichoke: 'temperate',

  // Cool = cold-hardy; love cool climates, fine in temperate.
  cabbage: 'cool', kale: 'cool', turnip: 'cool', rutabaga: 'cool',
  radish: 'cool', leek: 'cool', rhubarb: 'cool', spinach: 'cool',
  broccoli: 'cool', cauliflower: 'cool', cranberry: 'cool',
  greenonion: 'cool',
  onion: 'cool', garlic: 'cool', lettuce: 'cool', mustard: 'cool',
  celery: 'cool', asparagus: 'cool', barley: 'cool', oat: 'cool',
  hops: 'cool', elderberry: 'cool', currant: 'cool',
  blackberry: 'cool', raspberry: 'cool', blueberry: 'cool',
  strawberry: 'cool', kiwi: 'cool',

  saguaro: 'arid',

  rice: 'aquatic',
}

const SEED_ID_OVERRIDES = {
  coffee_beans: 'croptopia:coffee_seed',
  tea_leaves:   'croptopia:tea_seed',
  vanilla:      'croptopia:vanilla_seeds',
}

// Climate tags live in the `c:` namespace (NeoForge common tags); vanilla
// `minecraft:is_cold/hot/dry/wet/swamp/plains` do NOT exist as biome tags.
const BUCKET_TAGS = {
  tropical: {
    loves:   ['c:is_jungle', 'c:is_savanna', 'c:is_hot'],
    avoid:   ['c:is_cold', 'c:is_dry'],
    hostile: ['c:is_icy'],
  },
  temperate: {
    loves:   ['c:is_plains', 'c:is_forest'],
    avoid:   ['c:is_cold', 'c:is_jungle', 'c:is_hot', 'c:is_dry'],
    hostile: ['c:is_icy', 'c:is_badlands'],
  },
  cool: {
    loves:   ['c:is_cold', 'c:is_taiga'],
    avoid:   ['c:is_hot', 'c:is_dry'],
    hostile: ['c:is_desert', 'c:is_badlands'],
  },
  arid: {
    loves:   ['c:is_desert', 'c:is_dry', 'c:is_badlands', 'c:is_savanna'],
    avoid:   ['c:is_jungle', 'c:is_cold', 'c:is_wet'],
    hostile: ['c:is_icy', 'c:is_swamp'],
  },
  aquatic: {
    loves:   ['c:is_swamp', 'c:is_river', 'c:is_wet', 'c:is_jungle'],
    avoid:   ['c:is_dry', 'c:is_cold'],
    hostile: ['c:is_desert', 'c:is_badlands', 'c:is_icy'],
  },
}

const BUCKET_LABELS = {
  tropical:  'Loves warm, humid biomes',
  temperate: 'Prefers mild climates',
  cool:      'Loves cool, forested biomes',
  arid:      'Thrives in dry heat',
  aquatic:   'Loves wetlands',
}

const STATE_LABELS = {
  thriving:       { text: 'Thriving',       color: 'green' },
  growing_well:   { text: 'Growing well',   color: 'aqua' },
  growing:        { text: 'Growing',        color: 'gray' },
  struggling:     { text: 'Struggling',     color: 'gold' },
  barely_growing: { text: 'Withering',      color: 'red' },
}

const DYNAMIC_ID = 'logisticalpack:biome_growth'

const SEED_TO_CROP = {}
const SEED_TO_LABEL = {}
Object.keys(CROP_BUCKETS).forEach(cropName => {
  const seedId = SEED_ID_OVERRIDES[cropName] || ('croptopia:' + cropName + '_seed')
  SEED_TO_CROP[seedId] = cropName
  SEED_TO_LABEL[seedId] = BUCKET_LABELS[CROP_BUCKETS[cropName]]
})

const HAS_CROP_TAGS = {}
Object.keys(CROP_BUCKETS).forEach(cropName => {
  HAS_CROP_TAGS[cropName] = 'croptopia:has_crop/' + cropName
})

// Per-crop additional hostile biome tags merged with the bucket's hostile list.
// Mirrors CROP_HOSTILE_EXTRA in server_scripts/biome_growth_tick.js — keep in sync.
const CROP_HOSTILE_EXTRA = {
  // Strict tropical (frost = death)
  pineapple:    ['c:is_cold', 'c:is_dry'],
  coffee_beans: ['c:is_cold'],
  vanilla:      ['c:is_cold', 'c:is_dry'],
  pepper:       ['c:is_cold', 'c:is_dry'],
  basil:        ['c:is_cold'],
  chile_pepper: ['c:is_cold'],
  ginger:       ['c:is_cold', 'c:is_dry'],
  turmeric:     ['c:is_cold', 'c:is_dry'],
  yam:          ['c:is_cold'],
  sweetpotato:  ['c:is_cold'],
  bellpepper:   ['c:is_cold'],

  // Strict temperate (warm-season frost-killers)
  tomato:     ['c:is_cold'],
  tomatillo:  ['c:is_cold'],
  eggplant:   ['c:is_cold'],
  cucumber:   ['c:is_cold'],
  squash:     ['c:is_cold'],
  zucchini:   ['c:is_cold'],
  cantaloupe: ['c:is_cold'],
  honeydew:   ['c:is_cold'],
  corn:       ['c:is_cold'],
  soybean:    ['c:is_cold'],
  blackbean:  ['c:is_cold'],
  greenbean:  ['c:is_cold'],
  artichoke:  ['c:is_cold'],

  olive:      ['c:is_cold', 'c:is_wet'],
  saguaro:    ['c:is_cold', 'c:is_wet'],
}

function getBucketRelation(cropName, biomeTags) {
  const bucket = CROP_BUCKETS[cropName]
  if (!bucket) return 'compatible'
  const keys = BUCKET_TAGS[bucket]
  if (biomeHasAny(biomeTags, keys.hostile)) return 'hostile'
  const extra = CROP_HOSTILE_EXTRA[cropName]
  if (extra && biomeHasAny(biomeTags, extra)) return 'hostile'
  if (biomeHasAny(biomeTags, keys.avoid)) return 'avoid'
  if (biomeHasAny(biomeTags, keys.loves)) return 'loves'
  return 'compatible'
}

function getStateName(cropName, biomeHolder) {
  const bucket = CROP_BUCKETS[cropName]
  if (!bucket) return 'growing'
  const biomeTags = biomeTagSet(biomeHolder)
  const relation = getBucketRelation(cropName, biomeTags)
  const inHasCrop = !!biomeTags[HAS_CROP_TAGS[cropName]]
  if (inHasCrop) {
    if (relation === 'loves') return 'thriving'
    if (relation === 'compatible') return 'growing_well'
    return 'growing'
  }
  if (relation === 'loves') return 'growing_well'
  if (relation === 'compatible') return 'growing'
  if (relation === 'avoid') return 'struggling'
  return 'barely_growing'
}

function prettyBiomeName(biomeHolder) {
  try {
    const id = biomeHolder.unwrapKey().get().location().toString()
    const path = id.substring(id.indexOf(':') + 1)
    const parts = path.split('_')
    let result = ''
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) result += ' '
      const w = parts[i]
      result += w.charAt(0).toUpperCase() + w.substring(1)
    }
    return result
  } catch (e) {
    return 'this biome'
  }
}

ItemEvents.modifyTooltips(event => {
  Object.keys(SEED_TO_CROP).forEach(seedId => {
    const label = SEED_TO_LABEL[seedId]
    event.modify(seedId, builder => {
      builder.add([Text.gray(label).italic()])
      builder.dynamic(DYNAMIC_ID)
    })
  })
})

// JEI / EMI / REI info pane — static, biome-independent.
// Shows the climate label colored by bucket so players browsing the recipe
// viewer can discover each crop's preferred climate.
// Colors use the dark_* variants because JEI's info pane has a white
// background; lighter colors (aqua, green, gray) wash out and become unreadable.
const BUCKET_COLOR = {
  tropical:  'dark_red',
  temperate: 'dark_green',
  cool:      'dark_blue',
  arid:      'gold',
  aquatic:   'dark_aqua',
}

const BUCKET_BLURB = {
  tropical:  'Best in jungles and savannas. Suffers in cold biomes.',
  temperate: 'Best in plains and forests. Suffers in deep jungle and badlands.',
  cool:      'Best in taigas and cold biomes. Suffers in hot dry biomes.',
  arid:      'Best in badlands and deserts. Suffers in wetlands.',
  aquatic:   'Best in swamps, rivers, and jungles. Suffers in dry biomes.',
}

RecipeViewerEvents.addInformation('item', event => {
  Object.keys(SEED_TO_CROP).forEach(seedId => {
    const cropName = SEED_TO_CROP[seedId]
    const bucket = CROP_BUCKETS[cropName]
    event.add(seedId, [
      Text.of(BUCKET_LABELS[bucket]).color(BUCKET_COLOR[bucket]),
      Text.of(BUCKET_BLURB[bucket]).color('dark_gray'),
    ])
  })
})

ItemEvents.dynamicTooltips(DYNAMIC_ID, event => {
  let itemId = null
  try { itemId = String(event.item.id) } catch (e) {}
  if (!itemId) return

  const cropName = SEED_TO_CROP[itemId]
  if (!cropName) return

  let player = null
  let level = null
  try {
    const mc = Minecraft.getInstance()
    player = mc.player
    level = mc.level
  } catch (e) {}
  if (!player || !level) return

  try {
    const biomeHolder = level.getBiome(player.blockPosition())
    const biomeName = prettyBiomeName(biomeHolder)
    const stateName = getStateName(cropName, biomeHolder)
    const info = STATE_LABELS[stateName]
    event.add([Text.of(info.text + ' in ' + biomeName).color(info.color)])
  } catch (e) {}
})

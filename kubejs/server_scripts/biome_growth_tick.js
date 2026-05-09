// =============================================================================
// Biome-aware crop growth speed (server side)
//   - Slows random ticks for crops planted outside their climate
//   - Adds bonus age increments in ideal climate
//   - Crops in the croptopia:has_crop/<crop> tag clamp to at least 1.0x
// Edit CROP_BUCKETS to recategorize a crop. Edit BUCKET_TAGS to retune climate.
// =============================================================================

// Inline class lookups — top-level `const Blocks` collides with other scripts.
const DEAD_BUSH_STATE = Java.loadClass('net.minecraft.world.level.block.Blocks').DEAD_BUSH.defaultBlockState()
const AGE_7 = Java.loadClass('net.minecraft.world.level.block.state.properties.BlockStateProperties').AGE_7
const BiomeGrowthJade = Java.loadClass('com.fatcrackle.biomegrowthjade.BiomeGrowthJade')

// Maps the climate tag that killed the crop to a player-facing reason.
// First match wins; order in BUCKET_TAGS.hostile / CROP_HOSTILE_EXTRA matters.
const TAG_DEATH_REASON = {
  'c:is_icy':      'frozen solid',
  'c:is_cold':     'too cold',
  'c:is_taiga':    'too cold',
  'c:is_hot':      'too hot',
  'c:is_savanna':  'too hot',
  'c:is_desert':   'too dry',
  'c:is_dry':      'too dry',
  'c:is_badlands': 'too harsh',
  'c:is_wet':      'too humid',
  'c:is_swamp':    'too wet',
  'c:is_jungle':   'too humid',
}

function prettyBiomeName(biomeId) {
  if (!biomeId || biomeId === 'unknown') return 'This biome'
  var colon = biomeId.indexOf(':')
  var path = colon >= 0 ? biomeId.substring(colon + 1) : biomeId
  var parts = path.split('_')
  var out = ''
  for (var i = 0; i < parts.length; i++) {
    if (i > 0) out += ' '
    var w = parts[i]
    if (w.length > 0) out += w.charAt(0).toUpperCase() + w.substring(1)
  }
  return out
}

function deathReason(cropName, biomeTags, biomeId) {
  var biomePrefix = prettyBiomeName(biomeId)
  var bucket = CROP_BUCKETS[cropName]
  if (!bucket) return biomePrefix + ' unsuitable for crops'
  // Check bucket-level hostile tags first, then per-crop extras (these are
  // the lists that gave us mult=0.2 in getBucketRelation).
  var bucketHostile = BUCKET_TAGS[bucket].hostile
  for (var i = 0; i < bucketHostile.length; i++) {
    if (biomeTags[bucketHostile[i]]) return biomePrefix + ' ' + (TAG_DEATH_REASON[bucketHostile[i]] || 'unsuitable')
  }
  var extra = CROP_HOSTILE_EXTRA[cropName]
  if (extra) {
    for (var j = 0; j < extra.length; j++) {
      if (biomeTags[extra[j]]) return biomePrefix + ' ' + (TAG_DEATH_REASON[extra[j]] || 'unsuitable')
    }
  }
  return biomePrefix + ' unsuitable for crops'
}

function biomeIdString(block) {
  var v = null
  try { v = block.biomeId } catch (e) {}
  return v ? String(v) : 'unknown'
}

// Death threshold — multipliers <= this count as "Barely growing" and trigger
// the death roll. Currently only mult=0.2 hits this; struggling at 0.5 doesn't.
const DEATH_THRESHOLD = 0.3
const DEATH_PROBABILITY = 0.5

// We compare biome tags via the biome holder's own tags() stream rather than
// calling Holder.is(...) — Rhino can't resolve that method's overloads when
// given a TagKey, and kjs$isTag isn't visible to Rhino on Holder.Reference.
// Iterating tags() returns Stream<TagKey<T>>, easy to walk via iterator().
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

// HAS_CROP tag id strings, keyed by crop name.
const HAS_CROP_TAGS = {}
for (const cropName in CROP_BUCKETS) {
  HAS_CROP_TAGS[cropName] = `croptopia:has_crop/${cropName}`
}

// Per-crop additional hostile biome tags merged with the bucket's hostile list.
// Use for plants that would 100% die in those climates IRL (e.g. pineapple in
// frost) — promotes them from "Struggling" (0.5x) to "Barely growing" (0.2x).
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
  // tea_leaves, peanut — keep bucket buffer

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
  // grape — keep bucket buffer (cold-hardy varieties)

  // Mediterranean specifics
  olive:      ['c:is_cold', 'c:is_wet'],

  // Desert cactus
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

function getMultiplier(cropName, biomeHolder) {
  const bucket = CROP_BUCKETS[cropName]
  if (!bucket) return 1.0

  const biomeTags = biomeTagSet(biomeHolder)
  const relation = getBucketRelation(cropName, biomeTags)
  const inHasCrop = !!biomeTags[HAS_CROP_TAGS[cropName]]

  if (inHasCrop) {
    if (relation === 'loves') return 1.5
    if (relation === 'compatible') return 1.2
    return 1.0
  }
  if (relation === 'loves') return 1.2
  if (relation === 'compatible') return 1.0
  if (relation === 'avoid') return 0.5
  return 0.2
}

// Approximate vanilla CropBlock growth probability per random tick at base
// growth speed (no farmland bonus). Vanilla rolls `nextInt(25/speed+1) == 0`,
// so at speed=1.0 the chance is 1/26 ≈ 0.038. We calibrate the bonus rate
// against this so mult=1.5 means roughly 1.5× the vanilla rate, not 13×.
const VANILLA_TICK_P = 1.0 / 26.0

function handleRandomTick(event, cropName) {
  const block = event.block
  const biomeHolder = event.level.getBiome(block.pos)
  const mult = getMultiplier(cropName, biomeHolder)

  // Barely-growing crops have a chance to die into a dead bush per random tick.
  // Has to come before event.cancel() since cancel throws and exits the function.
  if (mult <= DEATH_THRESHOLD && Math.random() < DEATH_PROBABILITY) {
    var reason = deathReason(cropName, biomeTagSet(biomeHolder), biomeIdString(block))
    BiomeGrowthJade.recordDeath(event.level, block.pos, cropName, reason)
    block.setBlockState(DEAD_BUSH_STATE, 3)
    return
  }

  if (mult < 1.0) {
    // event.cancel() throws EventExit (default method on KubeEvent) which
    // propagates to the EventHandlerContainer, marks the result INTERRUPT_FALSE,
    // and the BlockStateBaseMixin then cancels vanilla's randomTick.
    if (Math.random() > mult) event.cancel()
  } else if (mult > 1.0) {
    // Bonus advance, calibrated against vanilla's per-tick advance chance.
    // We use BlockState.setValue(AGE_7, ...) instead of kjs$getBlock — the kjs$
    // methods aren't visible to Rhino, so the old code silently no-op'd.
    if (Math.random() < (mult - 1.0) * VANILLA_TICK_P) {
      const state = block.blockState
      if (state.hasProperty(AGE_7)) {
        const age = state.getValue(AGE_7)
        if (age < 7) block.setBlockState(state.setValue(AGE_7, age + 1), 2)
      }
    }
  }
}

Object.keys(CROP_BUCKETS).forEach(cropName => {
  const blockId = 'croptopia:' + cropName + '_crop'
  BlockEvents.randomTick(blockId, event => handleRandomTick(event, cropName))
})

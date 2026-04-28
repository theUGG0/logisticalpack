# Radial Biomes — tier configuration

Each file in this folder defines ONE biome tier — a ring at a given distance from world origin.
Files are loaded in filename-sort order, so the `tier_0_*`, `tier_1_*` naming is recommended.

## Schema

```json
{
  "name": "human readable tier name (shown in logs)",
  "minDistance": 0,
  "maxDistance": 3000,
  "maxDistanceInfinite": false,
  "allowedBiomes": [
    "minecraft:plains",
    "terralith:haze_mountain"
  ],
  "allowedPrefixes": [
    "terralith:lavender_"
  ],
  "fallbackBiomes": [
    "minecraft:plains",
    "minecraft:forest"
  ]
}
```

### Fields

| Field                  | Type         | Required | Description                                                                                           |
|------------------------|--------------|----------|-------------------------------------------------------------------------------------------------------|
| `name`                 | string       | no       | Used only in log output.                                                                              |
| `minDistance`          | number       | yes      | Inner radius of the ring, in blocks from (0, 0).                                                      |
| `maxDistance`          | number       | no*      | Outer radius of the ring, in blocks. *Either this or `maxDistanceInfinite` should be set.             |
| `maxDistanceInfinite`  | boolean      | no       | If `true`, the tier extends forever. Use for the outermost tier.                                      |
| `allowedBiomes`        | array of ids | yes      | Biome IDs permitted in this ring. If the delegate biome source returns one of these, it's kept.       |
| `allowedPrefixes`      | array        | no       | If a biome ID starts with any of these strings, it's allowed. Handy for catching whole mod families.  |
| `fallbackBiomes`       | array of ids | yes      | When the delegate returns a biome not in the allowed set, one of these is picked deterministically.   |

## How the replacement works

For every biome query `(x, y, z)`:

1. We compute `distance = sqrt(x² + z²)` in blocks.
2. We find the first tier whose `[minDistance, maxDistance)` contains that distance.
3. We ask the underlying biome source (Terralith / vanilla / Tectonic) what biome it would pick. Its
   decision already incorporates our continentalness and (optionally) temperature biases.
4. If that biome is in the tier's `allowedBiomes` set, or its ID starts with one of `allowedPrefixes`,
   we keep it. Otherwise we pick one of `fallbackBiomes` deterministically based on coordinates
   (same coords → same biome, so the world is stable across reloads).

## Customization tips

**Wider tiers, different theme:** Change `minDistance` / `maxDistance`. For example, to push hot
biomes out to 5000 blocks, set `tier_1_temperate.json`'s `minDistance` to 5000 and
`tier_0_spawn.json`'s `maxDistance` to 5000.

**Adding a new biome to spawn ring:** Edit `tier_0_spawn.json` and add its ID to `allowedBiomes`.

**Blocking a biome entirely:** Remove it from every tier's `allowedBiomes` AND `allowedPrefixes`.
It'll get replaced with a fallback wherever it would have spawned.

**More tiers:** Just drop a new JSON in this folder. E.g. `tier_1_5_marsh.json` between tiers 1
and 2. Adjust `minDistance` / `maxDistance` so they don't overlap.

**Changing island behavior:** Edit `../main.json > continentalness`. `centerBias` closer to 0
means less island-dominance at spawn; more negative means heavier archipelago.

**Per-tier continentalness:** not supported in this default layout — it's a global curve. If you
need per-tier continentalness, edit `RadialClimateSampler.java` and pass a tier-aware function.

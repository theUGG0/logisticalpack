# Biome Growth Jade

Tiny NeoForge 1.21.1 mod that adds a colored climate verdict line to Jade
tooltips when looking at any Croptopia crop block. Mirrors the bucket logic
from the KubeJS scripts.

## Build

Requires JDK 21 (Prism's `java-runtime-delta` works).

Gradle is installed locally at `~/.local/share/gradle-bin/gradle-8.10/bin/gradle`.
The Jade jar is vendored under `libs/` since Modrinth's maven doesn't expose it
at the expected coordinate. To rebuild:

```
cd dev-mods/biome-growth-jade
~/.local/share/gradle-bin/gradle-8.10/bin/gradle build --no-daemon
```

Output jar lands at `build/libs/biome-growth-jade-1.0.0.jar`.

## Install

```
cp build/libs/biome-growth-jade-1.0.0.jar \
  ~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances/"Packwiz Development 1.21.1 NeoForge Workflow Template"/minecraft/mods/
```

Restart Minecraft. Hover any planted Croptopia crop — Jade should now show
a colored "Thriving / Growing well / Growing / Struggling / Barely growing in
[Biome]" line below the existing growth percentage.

## Updating Jade

If you upgrade Jade, replace `libs/Jade-*.jar` with the new jar and update
the filename in `build.gradle`'s `dependencies` block.

## Tuning

Edit `src/main/java/com/fatcrackle/biomegrowthjade/CropGrowthProvider.java`:

- `CROP_BUCKETS` — re-bucket a crop
- `BUCKET_TAGS` — change loves/avoid/hostile biome tag sets

Keep these in sync with `kubejs/server_scripts/biome_growth_tick.js` and
`kubejs/client_scripts/biome_growth_tooltip.js` so the displayed verdict
matches actual growth behavior.

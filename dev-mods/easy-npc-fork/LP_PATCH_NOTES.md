# LogisticalPack patches to Easy NPC

Fork of MarkusBordihn's `BOs-Easy-NPC` repository, branch `1.21.1`, at
commit `aa018ef` (= upstream release 6.13.0). Forked under MIT (the
upstream license — see `LICENSE.md`). Only the code is licensed; assets
remain under their original terms.

## Why the fork

Upstream Easy NPC has a known unfixed bug — issue
[#305](https://github.com/MarkusBordihn/BOs-Easy-NPC/issues/305) (closed
as Not Planned) — where preset-imported NPCs spawn with valid `Offers`
NBT but their right-click trading interaction silently fails. Reproduced
and root-caused via bytecode + source analysis: the deserialisation path
loads offers from NBT, but `setTradingOffers` calls
`updateMerchantTradingOffers` which wipes the
`merchantTradingOffers` field unless `TradingDataSet.tradingType` is
BASIC / ADVANCED / CUSTOM. Default loaded type is NONE, so offers get
wiped immediately after they're set.

A KubeJS-side workaround was attempted first (Java reflection +
classfilter override jar + delayed-retry queue, ~250 lines of JS); it
worked but was brittle and dependent on Easy NPC internals not being
renamed. The upstream fix is simpler and lower-risk.

## The patch

Single change, `core/Common/src/main/java/de/markusbordihn/easynpc/entity/easynpc/data/TradingDataCapable.java`,
inside `readAdditionalTradingData`. After loading `Offers` from NBT,
promote `TradingType.NONE` → `TradingType.BASIC` so the subsequent
`updateMerchantTradingOffers` call doesn't wipe the field. Also adds an
`isEmpty()` guard so we don't promote the type for an empty offers list.

```java
if (offers != null && !offers.isEmpty()) {
  log.debug("Loading trading offers {} for {}", offers, this);
  TradingDataSet tradingDataSet = this.getTradingDataSet();
  if (tradingDataSet != null && tradingDataSet.isType(TradingType.NONE)) {
    tradingDataSet.setType(TradingType.BASIC);
    this.setTradingDataSet(tradingDataSet);
  }
  this.setTradingOffers(offers);
}
```

## Build

Requires JDK 21. From this directory:

```
cd core
JAVA_HOME=<path-to-jdk-21> ./gradlew :NeoForge:build --no-daemon
```

Output: `core/NeoForge/build/libs/easy_npc-neoforge-1.21.1-6.13.0.jar`

Rename to `easy_npc-neoforge-1.21.1-6.13.0-lp1.jar` (the `-lp1` suffix
distinguishes it from upstream in mod listings) and copy into the repo's
`mods/` directory. Update `mods/easy-npc-core.pw.toml` with the new
sha512.

## Tracking upstream

The fork is a shallow clone of branch `1.21.1`. To pull upstream
changes:

```
cd dev-mods/easy-npc-fork
git fetch origin 1.21.1
git rebase origin/1.21.1
```

Re-apply the patch if the upstream rebase loses it (single hunk in
`TradingDataCapable.java`). When upstream fixes #305 properly, drop this
fork and revert `easy-npc-core.pw.toml` back to the Modrinth URL.

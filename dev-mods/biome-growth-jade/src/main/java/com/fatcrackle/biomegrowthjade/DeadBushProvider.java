package com.fatcrackle.biomegrowthjade;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.level.chunk.LevelChunk;
import snownee.jade.api.BlockAccessor;
import snownee.jade.api.IBlockComponentProvider;
import snownee.jade.api.ITooltip;
import snownee.jade.api.config.IPluginConfig;

public class DeadBushProvider implements IBlockComponentProvider {
    public static final DeadBushProvider INSTANCE = new DeadBushProvider();
    private static final ResourceLocation UID = ResourceLocation.fromNamespaceAndPath(BiomeGrowthJade.MODID, "death_record");

    @Override
    public void appendTooltip(ITooltip tooltip, BlockAccessor accessor, IPluginConfig config) {
        System.out.println("[BiomeGrowthJade] DeadBushProvider check at " + accessor.getPosition().toShortString() + " block=" + accessor.getBlock());
        if (!(accessor.getLevel().getChunkAt(accessor.getPosition()) instanceof LevelChunk chunk)) {
            System.out.println("[BiomeGrowthJade] not a LevelChunk");
            return;
        }
        DeathRecords records = chunk.getData(BiomeGrowthJade.DEATH_RECORDS);
        DeathRecord record = records.get(accessor.getPosition());
        System.out.println("[BiomeGrowthJade] record lookup: " + record);
        if (record == null) return;

        tooltip.add(Component.literal("Withered " + prettyName(record.crop())).withStyle(ChatFormatting.RED));
        // record.biome() now stores a death reason like "too cold" / "too humid".
        // Older saved records may still hold a biome id ("minecraft:swamp") or
        // "unknown" — show those as a fallback "climate: ..." line.
        String reason = record.biome();
        if (reason.contains(":") || reason.equals("unknown") || reason.equals("Unknown")) {
            tooltip.add(Component.literal("(climate: " + prettyBiomeName(reason) + ")").withStyle(ChatFormatting.DARK_GRAY));
        } else {
            tooltip.add(Component.literal("(" + reason + ")").withStyle(ChatFormatting.DARK_GRAY));
        }
    }

    @Override
    public ResourceLocation getUid() {
        return UID;
    }

    private static String prettyBiomeName(String id) {
        int colon = id.indexOf(':');
        return prettyName(colon >= 0 ? id.substring(colon + 1) : id);
    }

    private static String prettyName(String snake) {
        String[] parts = snake.split("_");
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            if (i > 0) sb.append(' ');
            String w = parts[i];
            if (w.isEmpty()) continue;
            sb.append(Character.toUpperCase(w.charAt(0))).append(w.substring(1));
        }
        return sb.toString();
    }
}

package com.fatcrackle.biomegrowthjade;

import net.minecraft.core.BlockPos;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.chunk.LevelChunk;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.attachment.AttachmentType;
import net.neoforged.neoforge.registries.DeferredRegister;
import net.neoforged.neoforge.registries.NeoForgeRegistries;

import java.util.function.Supplier;

@Mod(BiomeGrowthJade.MODID)
public class BiomeGrowthJade {
    public static final String MODID = "biomegrowthjade";

    public static final DeferredRegister<AttachmentType<?>> ATTACHMENTS =
            DeferredRegister.create(NeoForgeRegistries.ATTACHMENT_TYPES, MODID);

    public static final Supplier<AttachmentType<DeathRecords>> DEATH_RECORDS =
            ATTACHMENTS.register("death_records", () ->
                    AttachmentType.serializable(DeathRecords::new)
                            .sync(DeathRecords.STREAM_CODEC)
                            .build()
            );

    public BiomeGrowthJade(IEventBus modBus, ModContainer container) {
        ATTACHMENTS.register(modBus);
    }

    /** Called from KubeJS when a crop dies. Records what died and where. */
    public static void recordDeath(Level level, BlockPos pos, String cropName, String biomeName) {
        if (level.isClientSide) return;
        LevelChunk chunk = level.getChunkAt(pos);
        DeathRecords records = chunk.getData(DEATH_RECORDS);
        records.put(pos, new DeathRecord(cropName, biomeName));
        // setData (not just mutating in place) is what triggers the sync
        // handler to push the updated attachment to client-tracking players.
        chunk.setData(DEATH_RECORDS, records);
        chunk.setUnsaved(true);
        System.out.println("[BiomeGrowthJade] recordDeath: " + cropName + " at " + pos.toShortString() + " biome=" + biomeName);
    }
}

package com.fatcrackle.biomegrowthjade;

import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.Tag;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.neoforged.neoforge.common.util.INBTSerializable;

import java.util.HashMap;
import java.util.Map;

public class DeathRecords implements INBTSerializable<CompoundTag> {
    private final Map<Long, DeathRecord> records = new HashMap<>();

    public static final StreamCodec<RegistryFriendlyByteBuf, DeathRecords> STREAM_CODEC =
            StreamCodec.of(DeathRecords::encode, DeathRecords::decode);

    private static void encode(RegistryFriendlyByteBuf buf, DeathRecords self) {
        buf.writeVarInt(self.records.size());
        for (Map.Entry<Long, DeathRecord> e : self.records.entrySet()) {
            buf.writeLong(e.getKey());
            buf.writeUtf(e.getValue().crop());
            buf.writeUtf(e.getValue().biome());
        }
    }

    private static DeathRecords decode(RegistryFriendlyByteBuf buf) {
        DeathRecords out = new DeathRecords();
        int n = buf.readVarInt();
        for (int i = 0; i < n; i++) {
            long pos = buf.readLong();
            String c = buf.readUtf();
            String b = buf.readUtf();
            out.records.put(pos, new DeathRecord(c, b));
        }
        return out;
    }

    public void put(BlockPos pos, DeathRecord record) {
        records.put(pos.asLong(), record);
    }

    public DeathRecord get(BlockPos pos) {
        return records.get(pos.asLong());
    }

    public void remove(BlockPos pos) {
        records.remove(pos.asLong());
    }

    @Override
    public CompoundTag serializeNBT(HolderLookup.Provider provider) {
        ListTag list = new ListTag();
        for (Map.Entry<Long, DeathRecord> e : records.entrySet()) {
            CompoundTag entry = new CompoundTag();
            entry.putLong("p", e.getKey());
            entry.putString("c", e.getValue().crop());
            entry.putString("b", e.getValue().biome());
            list.add(entry);
        }
        CompoundTag tag = new CompoundTag();
        tag.put("entries", list);
        return tag;
    }

    @Override
    public void deserializeNBT(HolderLookup.Provider provider, CompoundTag tag) {
        records.clear();
        ListTag list = tag.getList("entries", Tag.TAG_COMPOUND);
        for (int i = 0; i < list.size(); i++) {
            CompoundTag entry = list.getCompound(i);
            records.put(entry.getLong("p"), new DeathRecord(entry.getString("c"), entry.getString("b")));
        }
    }
}

package com.fatcrackle.biomegrowthjade;

import net.minecraft.ChatFormatting;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.block.Block;
import snownee.jade.api.BlockAccessor;
import snownee.jade.api.IBlockComponentProvider;
import snownee.jade.api.ITooltip;
import snownee.jade.api.config.IPluginConfig;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class CropGrowthProvider implements IBlockComponentProvider {
    public static final CropGrowthProvider INSTANCE = new CropGrowthProvider();
    private static final ResourceLocation UID = ResourceLocation.fromNamespaceAndPath(BiomeGrowthJade.MODID, "climate");

    // Crop name -> climate bucket. Mirrors the KubeJS server script.
    private static final Map<String, String> CROP_BUCKETS = new HashMap<>();
    // Bucket -> { loves[], avoid[], hostile[] } biome tag keys.
    private static final Map<String, BucketTags> BUCKET_TAGS = new HashMap<>();
    // Crop name -> "has_crop/<crop>" biome tag key.
    private static final Map<String, TagKey<Biome>> HAS_CROP_TAGS = new HashMap<>();
    // Per-crop additional hostile biome tags merged with bucket hostile.
    // For plants that would 100% die in those climates IRL.
    private static final Map<String, List<TagKey<Biome>>> CROP_HOSTILE_EXTRA = new HashMap<>();

    static {
        addAll("tropical",  "pineapple","coffee_beans","vanilla","ginger","turmeric","tea_leaves",
                            "chile_pepper","pepper","yam","sweetpotato","peanut","basil","bellpepper");
        // Temperate = warm-season crops that suffer in frost.
        addAll("temperate", "tomato","tomatillo","corn","cucumber","squash","zucchini","eggplant",
                            "soybean","blackbean","greenbean","cantaloupe","honeydew",
                            "grape","olive","artichoke");
        // Cool = cold-hardy crops that love cool climates and tolerate temperate.
        addAll("cool",      "cabbage","kale","turnip","rutabaga","radish","leek","rhubarb","spinach",
                            "broccoli","cauliflower","cranberry","greenonion",
                            "onion","garlic","lettuce","mustard","celery","asparagus",
                            "barley","oat","hops","elderberry","currant","blackberry",
                            "raspberry","blueberry","strawberry","kiwi");
        addAll("arid",      "saguaro");
        addAll("aquatic",   "rice");

        // Climate tags live in the `c:` namespace (NeoForge common tags);
        // vanilla minecraft:is_cold/hot/dry/wet/swamp/plains do NOT exist.
        BUCKET_TAGS.put("tropical", new BucketTags(
            biomeTags("c:is_jungle", "c:is_savanna", "c:is_hot"),
            biomeTags("c:is_cold", "c:is_dry"),
            biomeTags("c:is_icy")));
        BUCKET_TAGS.put("temperate", new BucketTags(
            biomeTags("c:is_plains", "c:is_forest"),
            biomeTags("c:is_cold", "c:is_jungle", "c:is_hot", "c:is_dry"),
            biomeTags("c:is_icy", "c:is_badlands")));
        BUCKET_TAGS.put("cool", new BucketTags(
            biomeTags("c:is_cold", "c:is_taiga"),
            biomeTags("c:is_hot", "c:is_dry"),
            biomeTags("c:is_desert", "c:is_badlands")));
        BUCKET_TAGS.put("arid", new BucketTags(
            biomeTags("c:is_desert", "c:is_dry", "c:is_badlands", "c:is_savanna"),
            biomeTags("c:is_jungle", "c:is_cold", "c:is_wet"),
            biomeTags("c:is_icy", "c:is_swamp")));
        BUCKET_TAGS.put("aquatic", new BucketTags(
            biomeTags("c:is_swamp", "c:is_river", "c:is_wet", "c:is_jungle"),
            biomeTags("c:is_dry", "c:is_cold"),
            biomeTags("c:is_desert", "c:is_badlands", "c:is_icy")));

        // === Per-crop hostile extras ===
        // Strict tropical (frost = death)
        CROP_HOSTILE_EXTRA.put("pineapple",    biomeTags("c:is_cold", "c:is_dry"));
        CROP_HOSTILE_EXTRA.put("coffee_beans", biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("vanilla",      biomeTags("c:is_cold", "c:is_dry"));
        CROP_HOSTILE_EXTRA.put("pepper",       biomeTags("c:is_cold", "c:is_dry"));
        CROP_HOSTILE_EXTRA.put("basil",        biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("chile_pepper", biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("ginger",       biomeTags("c:is_cold", "c:is_dry"));
        CROP_HOSTILE_EXTRA.put("turmeric",     biomeTags("c:is_cold", "c:is_dry"));
        CROP_HOSTILE_EXTRA.put("yam",          biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("sweetpotato",  biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("bellpepper",   biomeTags("c:is_cold"));

        // Strict temperate (warm-season frost-killers)
        CROP_HOSTILE_EXTRA.put("tomato",     biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("tomatillo",  biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("eggplant",   biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("cucumber",   biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("squash",     biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("zucchini",   biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("cantaloupe", biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("honeydew",   biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("corn",       biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("soybean",    biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("blackbean",  biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("greenbean",  biomeTags("c:is_cold"));
        CROP_HOSTILE_EXTRA.put("artichoke",  biomeTags("c:is_cold"));

        // Mediterranean specifics
        CROP_HOSTILE_EXTRA.put("olive",      biomeTags("c:is_cold", "c:is_wet"));

        // Desert cactus
        CROP_HOSTILE_EXTRA.put("saguaro",    biomeTags("c:is_cold", "c:is_wet"));
    }

    private static void addAll(String bucket, String... names) {
        for (String n : names) {
            CROP_BUCKETS.put(n, bucket);
            HAS_CROP_TAGS.put(n, TagKey.create(Registries.BIOME,
                    ResourceLocation.fromNamespaceAndPath("croptopia", "has_crop/" + n)));
        }
    }

    private static List<TagKey<Biome>> biomeTags(String... names) {
        List<TagKey<Biome>> out = new java.util.ArrayList<>(names.length);
        for (String n : names) out.add(TagKey.create(Registries.BIOME, ResourceLocation.parse(n)));
        return out;
    }

    @Override
    public void appendTooltip(ITooltip tooltip, BlockAccessor accessor, IPluginConfig config) {
        Block block = accessor.getBlock();
        ResourceLocation blockId = BuiltInRegistries.BLOCK.getKey(block);
        if (blockId == null || !"croptopia".equals(blockId.getNamespace())) return;
        String path = blockId.getPath();
        if (!path.endsWith("_crop")) return;

        String cropName = path.substring(0, path.length() - "_crop".length());
        String bucket = CROP_BUCKETS.get(cropName);
        if (bucket == null) return;

        Holder<Biome> biomeHolder = accessor.getLevel().getBiome(accessor.getPosition());
        String relation = bucketRelation(cropName, bucket, biomeHolder);
        boolean inHasCrop = biomeHolder.is(HAS_CROP_TAGS.get(cropName));
        StateInfo info = stateFor(relation, inHasCrop);

        String biomeName = prettyBiomeName(biomeHolder);
        tooltip.add(Component.literal(info.text + " in " + biomeName).withStyle(info.color));
    }

    @Override
    public ResourceLocation getUid() {
        return UID;
    }

    private static String bucketRelation(String cropName, String bucket, Holder<Biome> biome) {
        BucketTags t = BUCKET_TAGS.get(bucket);
        for (TagKey<Biome> k : t.hostile) if (biome.is(k)) return "hostile";
        List<TagKey<Biome>> extra = CROP_HOSTILE_EXTRA.get(cropName);
        if (extra != null) for (TagKey<Biome> k : extra) if (biome.is(k)) return "hostile";
        for (TagKey<Biome> k : t.avoid)   if (biome.is(k)) return "avoid";
        for (TagKey<Biome> k : t.loves)   if (biome.is(k)) return "loves";
        return "compatible";
    }

    private static StateInfo stateFor(String relation, boolean inHasCrop) {
        if (inHasCrop) {
            if ("loves".equals(relation))      return new StateInfo("Thriving",     ChatFormatting.GREEN);
            if ("compatible".equals(relation)) return new StateInfo("Growing well", ChatFormatting.AQUA);
            return new StateInfo("Growing", ChatFormatting.GRAY);
        }
        if ("loves".equals(relation))      return new StateInfo("Growing well",   ChatFormatting.AQUA);
        if ("compatible".equals(relation)) return new StateInfo("Growing",        ChatFormatting.GRAY);
        if ("avoid".equals(relation))      return new StateInfo("Struggling",     ChatFormatting.GOLD);
        return new StateInfo("Withering", ChatFormatting.RED);
    }

    private static String prettyBiomeName(Holder<Biome> biome) {
        ResourceLocation id = biome.unwrapKey().map(k -> k.location()).orElse(null);
        if (id == null) return "this biome";
        String[] parts = id.getPath().split("_");
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            if (i > 0) sb.append(' ');
            String w = parts[i];
            if (w.isEmpty()) continue;
            sb.append(Character.toUpperCase(w.charAt(0))).append(w.substring(1));
        }
        return sb.toString();
    }

    private record StateInfo(String text, ChatFormatting color) {}

    private record BucketTags(List<TagKey<Biome>> loves, List<TagKey<Biome>> avoid, List<TagKey<Biome>> hostile) {}
}

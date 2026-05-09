package com.fatcrackle.biomegrowthjade;

import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.DeadBushBlock;
import snownee.jade.api.IWailaClientRegistration;
import snownee.jade.api.IWailaPlugin;
import snownee.jade.api.WailaPlugin;

@WailaPlugin
public class JadePlugin implements IWailaPlugin {
    @Override
    public void registerClient(IWailaClientRegistration registration) {
        registration.registerBlockComponent(CropGrowthProvider.INSTANCE, CropBlock.class);
        registration.registerBlockComponent(DeadBushProvider.INSTANCE, DeadBushBlock.class);
    }
}

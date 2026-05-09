ServerEvents.recipes(event => {
    event.remove({ id: 'bronze:crafting/bronze_ingot_from_tin_and_copper_ingots' })
    event.remove({ id: 'bronze:crafting/bronze_blend_from_copper_and_tin' })
    
    event.shapeless(
    Item.of('bronze:bronze_blend', 2), // arg 1: output
    [
        'bronze:raw_tin',
        '3x minecraft:raw_copper'
    ]
    )
})
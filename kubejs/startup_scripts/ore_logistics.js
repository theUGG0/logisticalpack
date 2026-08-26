ItemEvents.modification(event => {
  event.modify('minecraft:iron_ore', item => {
    item.maxStackSize = 2
  })
})
ServerEvents.recipes(event => {
  event.recipes.create.crushing(['create:cinder_flour', CreateItem.of('create:cinder_flour', 0.5)], 'minecraft:magma_block')
})
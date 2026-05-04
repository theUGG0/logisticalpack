// Right-click vanilla lit campfire with coal -> set signal_fire=true,
// consume 1 coal, extend expiry by HOT_DURATION. Lignite excluded by
// strict ID. Reversion clears signal_fire when expiry passes.

const HOT_DURATION = 60 * 20 * 3
const HOT_MAP_KEY = 'logisticalpack_coal_campfires'

function posKey(pos) {
  return pos.x + ',' + pos.y + ',' + pos.z
}

function parseKey(key) {
  const parts = key.split(',')
  return { x: parseInt(parts[0]), y: parseInt(parts[1]), z: parseInt(parts[2]) }
}

function getOrCreateMap(level) {
  const data = level.persistentData
  if (!data.contains(HOT_MAP_KEY)) data.put(HOT_MAP_KEY, {})
  return data.getCompound(HOT_MAP_KEY)
}

function clearCoalFlag(level, pos) {
  const block = level.getBlock(pos.x, pos.y, pos.z)
  if (block.id !== 'minecraft:campfire') return
  if (String(block.properties.signal_fire) !== 'true') return
  block.set('minecraft:campfire', {
    lit: block.properties.lit,
    facing: block.properties.facing,
    signal_fire: 'false',
    waterlogged: block.properties.waterlogged
  })
}

function scheduleRevert(server, level, pos, ticks) {
  server.scheduleInTicks(ticks, () => {
    const data = level.persistentData
    if (!data.contains(HOT_MAP_KEY)) {
      clearCoalFlag(level, pos)
      return
    }
    const map = data.getCompound(HOT_MAP_KEY)
    const key = posKey(pos)
    if (!map.contains(key)) {
      clearCoalFlag(level, pos)
      return
    }
    const expireAt = map.getLong(key)
    const now = Number(server.getTickCount())
    if (now >= expireAt) {
      map.remove(key)
      clearCoalFlag(level, pos)
    } else {
      scheduleRevert(server, level, pos, expireAt - now)
    }
  })
}

BlockEvents.rightClicked(event => {
  const block = event.block
  if (block.id !== 'minecraft:campfire') return
  if (String(block.properties.lit) !== 'true') return
  if (String(event.hand) !== 'MAIN_HAND') return

  const held = event.player.mainHandItem
  if (String(held.id) !== 'minecraft:coal') return

  if (!event.player.isCreative()) held.shrink(1)

  const map = getOrCreateMap(event.level)
  const key = posKey(block.pos)
  const now = Number(event.server.getTickCount())
  const base = map.contains(key) && map.getLong(key) > now ? map.getLong(key) : now
  map.putLong(key, base + HOT_DURATION)

  if (String(block.properties.signal_fire) !== 'true') {
    block.set('minecraft:campfire', {
      lit: 'true',
      facing: block.properties.facing,
      signal_fire: 'true',
      waterlogged: block.properties.waterlogged
    })
  }

  scheduleRevert(event.server, event.level, block.pos, HOT_DURATION)
})

ServerEvents.loaded(event => {
  event.server.getAllLevels().forEach(level => {
    const data = level.persistentData
    if (!data.contains(HOT_MAP_KEY)) return
    const map = data.getCompound(HOT_MAP_KEY)
    const now = Number(event.server.getTickCount())
    map.getAllKeys().forEach(key => {
      const expireAt = map.getLong(key)
      const remaining = expireAt > now ? expireAt - now : 1
      scheduleRevert(event.server, level, parseKey(key), remaining)
    })
  })
})

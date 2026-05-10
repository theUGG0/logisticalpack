// Town name pool + deterministic picker.
// nameForCoords(cx, cz) returns the same name for the same chunk coords every
// time — world seed not factored in deliberately, so towns get the same names
// across re-rolls of the same coords (useful for documentation / consistency).
//
// Cross-file namespace: KubeJS server_scripts share Rhino global scope, so
// a top-level `var LPTowns` works as a shared object across town_*.js files.
// We CANNOT use `global.LPTowns` — `global` is a Java Collections.emptyMap
// which throws UnsupportedOperationException on .put().

if (typeof LPTowns === 'undefined') var LPTowns = {}

;(function () {
  LPTowns.NAME_POOL = [
    'Ashford', 'Ashwood', 'Baybury', 'Birchgate', 'Blackbourne',
    'Blackmoor', 'Blackwater', 'Brambleby', 'Bramblewood', 'Briarcliff',
    'Brookhaven', 'Brookfield', 'Cliffmere', 'Coldbrook', 'Coldfell',
    'Crowmarsh', 'Crowmoor', 'Darkholme', 'Darkwater', 'Eastfell',
    'Eastgate', 'Edgewick', 'Elderhall', 'Elmbrook', 'Elmwood',
    'Fairburn', 'Fairfield', 'Falconreach', 'Farview', 'Fennshire',
    'Foxglove', 'Foxhollow', 'Frostfell', 'Frostmere', 'Galemoor',
    'Goldhall', 'Goldspire', 'Greenhill', 'Greenshore', 'Greyhall',
    'Greymarch', 'Grimwald', 'Hartford', 'Hawkridge', 'Hazelhurst',
    'Hazelwick', 'Helmsfield', 'Hightower', 'Highwatch', 'Hollowbrook',
    'Hollowdale', 'Ironreach', 'Ivywell', 'Larkmere', 'Larkstone',
    'Lichenford', 'Linwood', 'Longbridge', 'Longreach', 'Loomhaven',
    'Maesbury', 'Mapledale', 'Marshford', 'Marshwick', 'Meadowfern',
    'Mereholt', 'Millbrook', 'Millford', 'Mistgate', 'Mosshollow',
    'Northkeep', 'Northvale', 'Oakbrook', 'Oakdell', 'Oakenshire',
    'Oakridge', 'Penmoor', 'Pinegrove', 'Pinehurst', 'Quillshade',
    'Ravenscroft', 'Ravensgate', 'Redfen', 'Redford', 'Redhollow',
    'Ridgeport', 'Riverdale', 'Rivermarch', 'Rivermouth', 'Rockfall',
    'Rockwell', 'Rosehollow', 'Rosewatch', 'Saltbrook', 'Saltmere',
    'Sandstead', 'Sevenoaks', 'Shadowmere', 'Silverbrook', 'Silvermoor',
    'Slatebrook', 'Sloeford', 'Snowbridge', 'Snowfell', 'Southbridge',
    'Southreach', 'Sparrowfield', 'Springhaven', 'Sternhall', 'Stoneford',
    'Stonemarch', 'Stoneridge', 'Stormbridge', 'Stormgate', 'Stoutford',
    'Sunmoor', 'Sunwatch', 'Swallowmere', 'Swiftwater', 'Tarnhollow',
    'Thornhill', 'Thornwell', 'Tidemoor', 'Underhill', 'Vellaford',
    'Vinegrove', 'Wallrook', 'Wardgate', 'Watermill', 'Wayford',
    'Westbridge', 'Westmarch', 'Whitebrook', 'Whitecliff', 'Whitehaven',
    'Wickbrook', 'Wildmoor', 'Willowbrook', 'Willowdale', 'Windgate',
    'Windmere', 'Wolfsbridge', 'Wolffield', 'Wraithmoor', 'Wrenford',
    'Yewdale', 'Yewmoor', 'Aldenwood', 'Amberglen', 'Beechmere',
    'Bellhaven', 'Cinderfell', 'Clearwater', 'Cresthold', 'Driftmark',
    'Duskwell', 'Embergate', 'Fellwatch', 'Glimmerford', 'Hammerfall',
    'Heatherton', 'Holdfast', 'Hollowmere', 'Karthill', 'Lancegrove',
    'Marblegate', 'Mistwood', 'Moonbrook', 'Mossglen', 'Owlhollow',
    'Quartzhall', 'Quietwater', 'Roundhill', 'Saltspire', 'Shadehollow',
  ]

  LPTowns.nameForCoords = function (cx, cz) {
    let h = ((cx * 73856093) ^ (cz * 19349663)) >>> 0
    return LPTowns.NAME_POOL[h % LPTowns.NAME_POOL.length]
  }
})()

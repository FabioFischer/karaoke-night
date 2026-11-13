-- Karaoke listener: runs the commands REAPER's web interface cannot.
--
-- REAPER starts it from Scripts/__startup.lua in its resource folder, with
-- one line: dofile([[<clone>\reaper\scripts\karaoke_listener.lua]]). It also
-- runs as an action (Actions > Show action list > New action > Load
-- ReaScript). On every defer cycle it reads ExtState karaoke/cmd, runs the
-- command and writes the reply to karaoke/ack. The Stage's ReaperPlayer
-- (packages/stage/src/stage/player.py) is the other side. Setup:
-- reaper/README.md.
--
-- Commands (JSON):
--   {"id": 1, "op": "load", "manifest": "C:\\...\\manifest.json"}
--   {"id": 2, "op": "transpose", "semitones": 2}
--   {"id": 3, "op": "ping"}
--   {"id": 4, "op": "quit"}
-- Replies:
--   {"id": 1, "op": "load", "ok": true, "ms": 123.4}
--   {"id": 1, "op": "load", "ok": false, "error": "..."}

local SECTION = "karaoke"
local STEMS = { "lead", "backing", "drums", "bass", "guitar", "piano", "other" }
local TRACK_NAMES = {
  lead = "Lead", backing = "Backing", drums = "Drums", bass = "Bass",
  guitar = "Guitar", piano = "Piano", other = "Other",
}

-- The source of this chunk is "@" plus the path of this file, whether REAPER
-- runs it as an action or __startup.lua runs it with dofile. The action
-- context would name __startup.lua in the second case.
local script_dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or ""
local json = dofile(script_dir .. "lib/json.lua")

local function find_track(name)
  local wanted = name:lower()
  for i = 0, reaper.CountTracks(0) - 1 do
    local track = reaper.GetTrack(0, i)
    local _, track_name = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
    if track_name:lower() == wanted then return track end
  end
end

local function create_track(name)
  local index = reaper.CountTracks(0)
  reaper.InsertTrackAtIndex(index, true)
  local track = reaper.GetTrack(0, index)
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", name, true)
  return track
end

local function clear_items(track)
  for i = reaper.CountTrackMediaItems(track) - 1, 0, -1 do
    reaper.DeleteTrackMediaItem(track, reaper.GetTrackMediaItem(track, i))
  end
end

local function insert_stem(track, path, volume)
  local source = reaper.PCM_Source_CreateFromFile(path)
  if not source then error("cannot open stem " .. path, 0) end
  local item = reaper.AddMediaItemToTrack(track)
  local take = reaper.AddTakeToMediaItem(item)
  reaper.SetMediaItemTake_Source(take, source)
  reaper.SetMediaItemInfo_Value(item, "D_POSITION", 0)
  reaper.SetMediaItemInfo_Value(item, "D_LENGTH", (reaper.GetMediaSourceLength(source)))
  reaper.SetMediaItemInfo_Value(item, "D_VOL", volume)
end

local function read_file(path)
  local file, err = io.open(path, "rb")
  if not file then error("cannot open " .. tostring(err), 0) end
  local data = file:read("*a")
  file:close()
  return data
end

local function load_song(cmd)
  local manifest = json.decode(read_file(cmd.manifest))
  local song_dir = cmd.manifest:match("^(.*)[/\\]") or "."
  local volume = 10 ^ ((manifest.gain_db or 0) / 20)

  local paths = {}
  for _, stem in ipairs(STEMS) do
    local entry = manifest.stems[stem]
    if entry then paths[stem] = song_dir .. "/" .. entry.file end
  end

  -- An error up to here (unreadable manifest, bad JSON, no stems table)
  -- leaves the project untouched. An error inside the pcall below, such as a
  -- stem that cannot be opened, leaves the stem tracks it already cleared or
  -- filled as they are: the current song is partly replaced. Either way the
  -- error goes into the ack.
  -- Only tracks named after a stem are touched: the mic tracks stay intact.
  reaper.PreventUIRefresh(1)
  reaper.Undo_BeginBlock()
  local ok, err = pcall(function()
    for _, stem in ipairs(STEMS) do
      local track = find_track(TRACK_NAMES[stem]) or create_track(TRACK_NAMES[stem])
      clear_items(track)
      if paths[stem] then insert_stem(track, paths[stem], volume) end
    end
  end)
  reaper.Undo_EndBlock("Karaoke: load " .. tostring(manifest.title), -1)
  reaper.PreventUIRefresh(-1)
  reaper.TrackList_AdjustWindows(false)
  reaper.UpdateArrange()
  if not ok then error(err, 0) end
end

-- Absolute, not cumulative. Drums have no key, so they are never shifted.
local function transpose(cmd)
  for _, stem in ipairs(STEMS) do
    local track = stem ~= "drums" and find_track(TRACK_NAMES[stem])
    if track then
      for i = 0, reaper.CountTrackMediaItems(track) - 1 do
        local take = reaper.GetActiveTake(reaper.GetTrackMediaItem(track, i))
        if take then reaper.SetMediaItemTakeInfo_Value(take, "D_PITCH", cmd.semitones) end
      end
    end
  end
  reaper.UpdateArrange()
end

local OPS = {
  load = load_song,
  transpose = transpose,
  ping = function() end,
  quit = function() end,
}

-- Returns true when the loop should stop.
local function handle(raw)
  local start = reaper.time_precise()
  local cmd = json.decode(raw)
  local ok, err = false, "unknown op " .. tostring(cmd.op)
  if OPS[cmd.op] then ok, err = pcall(OPS[cmd.op], cmd) end
  local ack = { id = cmd.id, op = cmd.op, ok = ok }
  if ok then
    ack.ms = (reaper.time_precise() - start) * 1000
  else
    ack.error = tostring(err)
  end
  reaper.SetExtState(SECTION, "ack", json.encode(ack), false)
  return ok and cmd.op == "quit"
end

local function loop()
  local raw = reaper.GetExtState(SECTION, "cmd")
  if raw ~= "" then
    reaper.DeleteExtState(SECTION, "cmd", false)
    if handle(raw) then return end
  end
  reaper.defer(loop)
end

loop()

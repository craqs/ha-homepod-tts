# HomePod TTS - Custom Home Assistant Integration

A custom Home Assistant integration that plays TTS announcements with an optional chime sound on Apple HomePod, using **Google Gemini TTS** for speech synthesis and **Music Assistant / pyatv** for AirPlay streaming.

## Why?

Home Assistant's built-in `apple_tv` integration uses pyatv for AirPlay streaming, which delegates audio decoding to the `miniaudio` library. `miniaudio` frequently fails with `DecodeError('failed to init decoder', -1)` when streaming TTS audio to HomePod - a long-standing regression ([#71569](https://github.com/home-assistant/core/issues/71569), [#97075](https://github.com/home-assistant/core/issues/97075), [#123176](https://github.com/home-assistant/core/issues/123176)).

This integration **bypasses the bug entirely** by:
1. Generating TTS audio via Google Gemini API
2. Concatenating chime + TTS (+ optional music) with ffmpeg into a local WAV file
3. Streaming the local file via Music Assistant (synchronized AirPlay 2) or pyatv's `stream_file()` as fallback

## Features

- Chime + TTS announcements on HomePod via AirPlay
- **Music injection** - embed a `[music: prompt]` marker in any message to append a generated Lyria 3 music clip
- **`play_music` service** - generate and play standalone AI music via Gemini Lyria 3
- Google Gemini TTS with selectable model and voice (30 voices)
- Style prompts for controlling speech tone and pacing
- Dynamic range compression presets (off / light / moderate / heavy)
- Adjustable chime volume relative to TTS in the audio mix
- Speaker override - target any HomePod(s) from a single entity
- **HomePod mini volume scaling** - per-speaker volume compensation for quieter mini speakers via an entity label
- **Quiet mode** - lower volume, whisper prompt, and alternate speakers when a quiet-mode entity is active
- **Mute mode** - completely suppress announcements when a mute entity is active
- **Music Assistant health sensor** - surfaces whether configured speakers resolve in MA or fall back to pyatv
- **Operational entity attributes** - the notify entity exposes effective volume, speakers, mute/quiet state, cache and TTS settings for inspection
- TTS response caching with configurable max size and manual clear
- Volume control with automatic restore after playback
- Music Assistant transport for synchronized multi-room AirPlay 2
- Config flow UI - no YAML configuration needed

## Requirements

- Home Assistant 2024.4+
- Apple TV integration configured with your HomePod(s)
- Google Gemini API key ([get one here](https://aistudio.google.com/apikey))
- ffmpeg (included in HAOS by default)
- [Music Assistant](https://music-assistant.io/) add-on (optional, enables synchronized AirPlay 2)

## Installation

### HACS (Manual Repository)

1. Open HACS → Integrations → three-dot menu → **Custom repositories**
2. Add this repository URL, category: **Integration**
3. Search for "HomePod TTS" and install
4. Restart Home Assistant

### Manual

1. Copy `custom_components/homepod_tts/` to your HA config directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **HomePod TTS**
3. Select your default HomePod (from Apple TV integration entries)
4. Enter your Gemini API key

### Options (Settings → Devices → HomePod TTS → Configure)

| Option | Default | Description |
|---|---|---|
| TTS model | gemini-2.5-flash-preview-tts | Gemini model for speech synthesis |
| TTS voice | Aoede | One of 30 Gemini TTS voices |
| Style prompt | _(empty)_ | Default style instruction (e.g. "Say this calmly") |
| Chime | On | Play chime sound before announcement |
| Chime path | bundled soft chime | Path to custom chime MP3/WAV |
| Chime volume | 1.0 | Relative chime loudness in the mix (0.0-2.0) |
| Chime offset | 0 ms | Trim chime tail (negative) or add gap (positive) |
| Volume | 0.5 | HomePod playback volume (0.0-1.0) |
| HomePod mini volume scale | 1.0 | Volume multiplier applied to speakers labeled `homepod_mini` (see below). 1.0 = no scaling |
| Restore volume | On | Restore previous volume after playback |
| Compression | moderate | TTS compression preset (off/light/moderate/heavy) |
| Cache | On | Cache Gemini TTS responses to avoid re-generation |
| Cache max size | 200 MB | Automatic LRU eviction above this limit |
| Default speakers | _(from config)_ | One or more apple_tv media_player entities |
| Mute entity | _(none)_ | When this entity is `on`, all announcements are suppressed |
| Quiet mode entity | _(none)_ | When `on`, quiet mode overrides are applied |
| Quiet volume | 0.2 | Volume used in quiet mode |
| Quiet prompt | _(whisper)_ | Style prompt used in quiet mode |
| Quiet chime volume | 0.3 | Chime volume used in quiet mode |
| Quiet speakers | _(none)_ | Alternate speaker(s) used in quiet mode |

## Usage

### Custom Service

```yaml
action: homepod_tts.say
data:
  entity_id: notify.homepod_mini_hall
  message: "Paczka czeka w skrytce numer 5"
  volume_level: 0.55        # optional override
  chime: true               # optional override
  compress: moderate        # off | light | moderate | heavy
  prompt: "Say this in a gentle whisper"  # optional style
  speaker: media_player.homepod_bedroom  # optional target override
  chime_volume: 0.5         # optional, quieter chime
  offset: -1000             # optional, trim 1s from chime tail
  quiet: true               # optional, force quiet mode for this call
```

### Music Injection

Embed a `[music: prompt]` marker anywhere in your message to generate and append a Lyria 3 music clip. The marker is detected and removed from the spoken text; TTS and music are generated concurrently.

```yaml
action: homepod_tts.say
data:
  entity_id: notify.homepod_mini_hall
  message: "Kubuś, oto nowa piosenka dla Ciebie! [music: happy upbeat children's song about monkeys in the jungle]"
```

**Position detection:**
- Marker at the **end** → chime + TTS + music
- Marker at the **start** → chime + music + TTS
- Marker in the **middle** → treated as end; surrounding text is joined

### Standalone Music Playback

```yaml
action: homepod_tts.play_music
data:
  entity_id: notify.homepod_mini_hall
  prompt: "Upbeat jazz piano, sunny afternoon mood, 30 seconds"
  volume_level: 0.6         # optional
  speaker:                  # optional target override
    - media_player.homepod_l
    - media_player.homepod_r
```

Music is generated via Gemini Lyria 3 (`lyria-3-clip-preview`) and played via Music Assistant.

### Multi-Speaker

```yaml
action: homepod_tts.say
data:
  entity_id: notify.homepod_mini_hall
  message: "Uwaga, ważne ogłoszenie!"
  speaker:
    - media_player.homepod_mini_hall
    - media_player.homepod_mini_bedroom
    - media_player.homepod_living_room
```

When Music Assistant is available, speakers are played via synchronized AirPlay 2. Otherwise pyatv streams to each speaker in parallel via `asyncio.gather` (within ~100-200 ms).

### Clear TTS Cache

```yaml
action: homepod_tts.clear_cache
```

### Notify Entity

```yaml
action: notify.send_message
target:
  entity_id: notify.homepod_mini_hall
data:
  message: "Paczka czeka w skrytce numer 5"
```

### Automation Example - AI Bedtime Announcement

```yaml
- alias: Bedtime announcement
  trigger:
    - platform: time
      at: "20:00:00"
  action:
    - action: conversation.process
      data:
        text: "Generate a short bedtime message for a child"
        agent_id: conversation.claude
      response_variable: ai_response
    - action: homepod_tts.say
      data:
        entity_id: notify.homepod_mini_hall
        message: "{{ ai_response.response.speech.plain.speech }}"
        volume_level: 0.55
        prompt: "Say this softly and warmly, like a parent"
```

### Automation Example - AI Generated Song

```yaml
- alias: Sing a song
  trigger:
    - platform: state
      entity_id: input_text.song_topic
  action:
    - action: ai_task.generate_data
      data:
        task_name: Generate song lyrics
        model: conversation.claude
        instructions: >
          Write short, fun children's song lyrics in Polish about: {{ trigger.to_state.state }}.
          Output only the lyrics, no title or metadata.
      response_variable: song
    - action: homepod_tts.say
      data:
        entity_id: notify.homepod_mini_hall
        message: "{{ song.data.result }}"
        prompt: "Sing it with a cheerful, melodic voice"
```

## Audio Pipeline

```
Gemini TTS API → PCM (24kHz mono) ──────────────────────────────────────┐
                                                                         ├─ ffmpeg concat + resample → WAV (44.1kHz stereo)
Chime MP3 (with adelay 1s + volume adjust + offset trim/pad) ───────────┤                                   │
                                                                         │              Music Assistant or pyatv stream_file()
Lyria 3 MP3 (optional, from [music:] marker or play_music service) ─────┘                                   │
                                                                                                        HomePod
```

**Notes:**
- 1 second of silence is prepended to the first audio segment to avoid AirPlay buffering artifacts
- Compression is applied **only to TTS audio**, not the chime or music
- Gemini TTS PCM responses are cached (keyed on message + voice + model + prompt)
- ffmpeg runs every call so chime/compression/music changes never invalidate the TTS cache
- TTS and Lyria music generation run **concurrently** via `asyncio.gather` when music injection is used

## Available TTS Models

| Model | Description |
|---|---|
| `gemini-2.5-flash-preview-tts` | Low-latency, good quality (default) |
| `gemini-2.5-pro-preview-tts` | Higher fidelity, slower |
| `gemini-3.1-flash-tts-preview` | Latest generation, 70+ languages |

## Quiet Mode & Mute

**Mute entity** (e.g. `input_boolean.do_not_disturb`): when `on`, all `say` calls are silently dropped.

**Quiet mode entity** (e.g. `binary_sensor.quiet_mode`): when `on`, the following overrides apply automatically:
- Lower volume (configurable, default 0.2)
- Whisper-style prompt
- Reduced chime volume
- Alternate speaker list (e.g. only bedroom HomePod instead of whole-home)

Mute takes precedence over quiet mode. Both can be overridden per-call via the `quiet:` field on `homepod_tts.say`.

## HomePod mini Volume Scaling

HomePod minis are quieter than full-size HomePods at the same volume level. To compensate, assign the Home Assistant label **`homepod_mini`** to the mini's `media_player` entity (either the `apple_tv` entity *or* its Music Assistant entity - the integration cross-references them by MAC, so labeling one is enough).

When a labeled speaker is targeted, the configured **HomePod mini volume scale** multiplier is applied to that speaker's volume (clamped to 0.0-1.0). For example, a scale of `1.4` plays minis 40% louder than full-size speakers at the same requested volume. A scale of `1.0` disables scaling.

> Volume scaling is applied on the **pyatv** transport (per-device volume). The Music Assistant transport applies a single `announce_volume` to all targets, so a mixed mini / full-size group played via MA shares one volume.

## Music Assistant Health Sensor

When **default speakers** are configured, the integration adds a sensor (e.g. `sensor.<name>_ma_health`) that reports whether Music Assistant can serve all of them. This makes it easy to spot when playback has silently fallen back to pyatv - for example after an HA restart before Music Assistant has reconnected.

**States:**

| State | Meaning |
|---|---|
| `ok` | All configured default speakers resolve and are available in Music Assistant |
| `degraded` | MA is available but only some speakers are (partial synchronized playback) |
| `failed` | MA service is absent or no speakers are available → pyatv fallback is used |

**Attributes:** `transport` (`music_assistant` / `pyatv_fallback`), `available_count`, `configured_count`, `available`, `unavailable`, and `unresolved_macs` (configured MACs with no matching MA entity - useful for diagnosing discovery problems).

The sensor recomputes automatically as `media_player` entities change state.

## Entity Attributes

The notify entity exposes its effective operational configuration as state attributes for dashboards and troubleshooting, including: `tts_model`, `tts_voice`, `tts_prompt`, `volume`, `mini_volume_scale`, `effective_volume`, `chime_enabled`, `chime_volume`, `effective_chime_volume`, `compress_tts`, `default_speakers`, `effective_speakers`, `is_muted`, `is_quiet`, the quiet-mode overrides, and the cache settings. The `effective_*` values reflect quiet-mode overrides when quiet mode is active.

## License

MIT - see [LICENSE](LICENSE).

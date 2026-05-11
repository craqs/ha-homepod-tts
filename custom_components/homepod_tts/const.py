DOMAIN = "homepod_tts"
PLATFORMS = ["notify"]

CONF_HOMEPOD_IDENTIFIER = "homepod_identifier"
CONF_GEMINI_API_KEY = "gemini_api_key"
CONF_CHIME_ENABLED = "chime_enabled"
CONF_CHIME_PATH = "chime_path"
CONF_CHIME_VOLUME = "chime_volume"
CONF_DEFAULT_VOLUME = "default_volume"
CONF_RESTORE_VOLUME = "restore_volume"
CONF_TTS_VOICE = "tts_voice"
CONF_TTS_MODEL = "tts_model"
CONF_TTS_PROMPT = "tts_prompt"
CONF_COMPRESS_TTS = "compress_tts"
CONF_CHIME_OFFSET = "chime_offset"
CONF_CACHE_ENABLED = "cache_enabled"
CONF_CACHE_MAX_MB = "cache_max_mb"
CONF_DEFAULT_SPEAKERS = "default_speakers"
CONF_MUTE_ENTITY = "mute_entity"
CONF_QUIET_ENTITY = "quiet_entity"
CONF_QUIET_PROMPT = "quiet_prompt"
CONF_QUIET_CHIME_VOLUME = "quiet_chime_volume"
CONF_QUIET_VOLUME = "quiet_volume"
CONF_QUIET_SPEAKERS = "quiet_speakers"

DEFAULT_CHIME_ENABLED = True
DEFAULT_CHIME_VOLUME = 1.0
DEFAULT_VOLUME = 0.5
DEFAULT_RESTORE_VOLUME = True
DEFAULT_TTS_VOICE = "Aoede"
DEFAULT_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_TTS_PROMPT = ""
DEFAULT_COMPRESS_TTS = "moderate"
DEFAULT_CHIME_OFFSET = 0
DEFAULT_CACHE_ENABLED = True
DEFAULT_CACHE_MAX_MB = 200
DEFAULT_QUIET_PROMPT = "Speak in a soft, gentle whisper"
DEFAULT_QUIET_CHIME_VOLUME = 0.3
DEFAULT_QUIET_VOLUME = 0.25

CONF_MINI_VOLUME_SCALE = "mini_volume_scale"
DEFAULT_MINI_VOLUME_SCALE = 1.0
# HA label slug the user assigns to HomePod mini media_player entities
MINI_SPEAKER_LABEL = "homepod_mini"

GEMINI_TTS_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
)

GEMINI_TTS_MODELS = [
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
]

GEMINI_VOICES = [
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam",
    "Aoede", "Autonoe", "Callirrhoe", "Charon", "Despina",
    "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus",
    "Kore", "Laomedeia", "Leda", "Orus", "Puck",
    "Pulcherrima", "Rasalgethi", "Sadachbia", "Sadaltager",
    "Schedar", "Sulafat", "Umbriel", "Vindemiatrix",
    "Zephyr", "Zubenelgenubi",
]

COMPRESS_PRESETS = {
    "off": None,
    "light": (
        "acompressor=threshold=-15dB:ratio=2.5:attack=10"
        ":release=500:makeup=2:detection=rms"
    ),
    "moderate": (
        "acompressor=threshold=-19dB:ratio=5:attack=7"
        ":release=450:makeup=4:detection=rms"
    ),
    "heavy": (
        "acompressor=threshold=-24dB:ratio=8:attack=5"
        ":release=400:makeup=6:detection=rms"
    ),
}

GEMINI_TTS_SAMPLE_RATE = 24000
GEMINI_TTS_CHANNELS = 1

AIRPLAY_SAMPLE_RATE = 44100
AIRPLAY_CHANNELS = 2

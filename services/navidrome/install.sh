#!/bin/sh
set -e
# Service: Navidrome Music Server (SQLite WAL & Subsonic Tuned)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Navidrome Music Server on 127.0.0.1:4533 (Music: $STORAGE_BASE/music)..."

if ! command -v navidrome >/dev/null 2>&1; then
    apk_add navidrome navidrome-openrc ffmpeg sqlite
fi

addgroup navidrome media 2>/dev/null || true

mkdir -p /etc/navidrome /var/lib/navidrome "$STORAGE_BASE/music" /etc/conf.d
touch /var/log/navidrome.log
chown navidrome:media /var/log/navidrome.log 2>/dev/null || true
chown -R "${SAFE_USER:-$AUTH_USER}:media" "$STORAGE_BASE/music" 2>/dev/null || true
chmod 2775 "$STORAGE_BASE/music" 2>/dev/null || true

cat << 'EOF' > /etc/conf.d/navidrome
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
navidrome_configfile=/etc/navidrome/navidrome.toml
EOF

cat << EOF > /etc/navidrome/navidrome.toml
MusicFolder = "$STORAGE_BASE/music"
DataFolder = "/var/lib/navidrome"
DbPath = "/var/lib/navidrome/navidrome.db?_journal_mode=WAL&_synchronous=NORMAL&_busy_timeout=15000&_cache_size=-16384&_temp_store=MEMORY&_mmap_size=67108864&cache=shared"
Address = "127.0.0.1"
Port = 4533
LogLevel = "WARN"
LogFile = "/var/log/navidrome.log"

SessionTimeout = "72h"
EnableReplayGain = true
EnableNowPlaying = true
EnableScrobbleHistory = true
EnableSharing = true
EnableDownloads = true
DefaultTheme = "Dark"

ImageCacheSize = "100MiB"
TranscodingCacheSize = "150MiB"
MaxImageUploadSize = "10MB"
EnableTranscodingConfig = true
EnableScheduledDBAnalyze = true
CoverArtPriority = "embedded, cover.*, folder.*, front.*"
AutoImportPlaylists = true
PlaylistsPath = ".playlists:."

[Scanner]
Enabled = true
Schedule = "@every 6h"
WatcherWait = "5s"
Extractor = "taglib"
ScanOnStartup = false
IgnoreDotFolders = true
ArtistSplitExceptions = ["AC/DC", "AU/RA", "Simon & Garfunkel", "Earth, Wind & Fire"]
EOF

# Setup Smart Playlists directory and default Hi-Res lossless filter
mkdir -p "$STORAGE_BASE/music/.playlists"
cat << 'EOF' > "$STORAGE_BASE/music/.playlists/lossless-favorites.nsp"
{
  "name": "Hi-Res & Lossless: Свежее и Любимое",
  "comment": "Автоматическая подборка FLAC/ALAC треков с высоким рейтингом или добавленных за последние 30 дней",
  "all": [
    {
      "any": [
        { "is": { "loved": true } },
        { "gt": { "rating": 3 } },
        { "inTheLast": { "dateAdded": 30 } }
      ]
    },
    { "gt": { "bitDepth": 15 } }
  ],
  "sort": "dateAdded",
  "order": "desc",
  "limit": 250
}
EOF
chown -R "${SAFE_USER:-$AUTH_USER}:media" "$STORAGE_BASE/music/.playlists" 2>/dev/null || true
chmod 775 "$STORAGE_BASE/music/.playlists"

chown -R navidrome:media /var/lib/navidrome /etc/navidrome
rc-update add navidrome default >/dev/null 2>&1 || true
rc-service navidrome restart >/dev/null 2>&1 || true

for _ in 1 2 3; do
    if [ -f /var/lib/navidrome/navidrome.db ]; then
        sqlite3 /var/lib/navidrome/navidrome.db \
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-16384; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=67108864; PRAGMA busy_timeout=15000; PRAGMA wal_autocheckpoint=1000; PRAGMA optimize;" >/dev/null 2>&1 || true
        
        # Inject standard Opus 128k and MP3 192k transcoding presets for mobile bandwidth efficiency
        sqlite3 /var/lib/navidrome/navidrome.db <<'EOSQL' >/dev/null 2>&1 || true
CREATE TABLE IF NOT EXISTS transcoding (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_format TEXT NOT NULL,
    command TEXT NOT NULL,
    default_bit_rate INTEGER
);
INSERT OR REPLACE INTO transcoding (id, name, target_format, command, default_bit_rate)
VALUES (
    'opus-mobile-128',
    'Opus Mobile (High Efficiency)',
    'opus',
    'ffmpeg -i %s -map 0:a:0 -b:a %bk -v 0 -c:a libopus -vbr on -compression_level 10 -application audio -f opus -',
    128
);
INSERT OR REPLACE INTO transcoding (id, name, target_format, command, default_bit_rate)
VALUES (
    'mp3-compat-192',
    'MP3 Legacy Subsonic',
    'mp3',
    'ffmpeg -i %s -map 0:a:0 -b:a %bk -v 0 -c:a libmp3lame -q:a 2 -f mp3 -',
    192
);
EOSQL
        chown -R navidrome:media /var/lib/navidrome
        break
    fi
    sleep 1
done

deploy_caddy_snippet "navidrome"

log_success "Navidrome running on https://music.$BASE_DOMAIN (SQLite WAL + synchronous=NORMAL active)"

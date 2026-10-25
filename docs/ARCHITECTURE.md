# Архитектура Zero-Docker Alpine Homelab Appliance

Данный комплекс представляет собой эталонный автономный программный комплекс микросервисов для домашнего сервера или умного шлюза, построенный исключительно на базе **Alpine Linux 3.24** и легковесных контейнеров **LXC** под управлением **OpenRC**. 

Никакого Docker, Podman, systemd или тяжелых слоев виртуализации. Потребление оперативной памяти для всей системы из 8 изолированных контейнеров составляет **менее 500 МБ RAM**.

---

## 1. Топология сети и адресация

Все контейнеры подключены к единому сетевому мосту хоста `br0`, работающему в режиме L2-бриджа поверх основного физического интерфейса хоста (`eth0`).

```mermaid
flowchart TD
    WAN["Интернет / Провайдер"] --> HOST["Alpine Linux Host (testlab)\nIP: 192.168.1.147\nМост: br0 (eth0)\nTCP BBR + sysctl"]
    
    subgraph LAN ["Локальная сеть (192.168.1.0/24)"]
        CLIENTS["Клиенты (ПК, Смартфоны)\nDNS: 192.168.1.137\nШлюз: 192.168.1.140"]
        
        CADDY["caddybox (192.168.1.51)\nCaddy 2 Reverse Proxy\nHTTPS 443 (Local PKI)\nSmart Redir (/ui, /web)"]
        ADGUARD["adguardbox (192.168.1.137)\nAdGuard Home DoH/DoT :53\nFilter 7 (RU) + Wildcard *.lan"]
        PROXY["proxybox (192.168.1.140)\nsing-box 1.14 TUN\nRuNet Direct + VLESS"]
        
        VAULT["vaultbox (192.168.1.100)\nVaultwarden :8000\nPasskeys & WebAuthn"]
        GIT["gitbox (192.168.1.132)\nForgejo Git :3000\nROOT_URL git.lan"]
        MATRIX["matrixbox (192.168.1.93)\nConduit Matrix :6167\nRocksDB v18"]
        MEDIA["mediabox (192.168.1.150)\nSamba 4 + WSDD\nNavidrome + Transmission"]
        YTDL["ytdlbox (192.168.1.155)\nPython 3 Downloader :8080\n(Шлюз: 192.168.1.140)"]
    end

    HOST --> CADDY
    HOST --> ADGUARD
    HOST --> PROXY
    HOST --> VAULT
    HOST --> GIT
    HOST --> MATRIX
    HOST --> MEDIA
    HOST --> YTDL

    CLIENTS -->|DNS Запросы| ADGUARD
    CLIENTS -->|HTTPS Запросы *.lan| CADDY
    CLIENTS -->|Шлюз / Интернет| PROXY
    
    CADDY -->|Reverse Proxy :8000| VAULT
    CADDY -->|Reverse Proxy :3000| GIT
    CADDY -->|Reverse Proxy :6167| MATRIX
    CADDY -->|Reverse Proxy :4533| MEDIA
    CADDY -->|Reverse Proxy :9091| MEDIA
    CADDY -->|Reverse Proxy :8080| YTDL
    CADDY -->|Reverse Proxy :9090| PROXY
    CADDY -->|Reverse Proxy :3000| ADGUARD
```

---

## 2. Спецификация микросервисов

| Контейнер | IP | Роль | Внутренние порты | Доменное имя (HTTPS) | Авторизация |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`caddybox`** | `192.168.1.51` | Шлюз TLS, Local PKI, Редиректы | 80, 443 | `*.lan` | — |
| **`adguardbox`** | `192.168.1.137` | DoH/DoT DNS + RU AdBlock | 53 (DNS), 3000 (UI) | `https://adguard.lan/` | Basic Auth |
| **`proxybox`** | `192.168.1.140` | sing-box 1.14 TUN + Metacubexd | 9090 (API/UI), 172.19.0.1 (tun) | `https://proxy.lan/` (-> `/ui/`) | Basic Auth |
| **`vaultbox`** | `192.168.1.100` | Vaultwarden (FIDO2/Passkeys) | 8000 (HTTP) | `https://vault.lan/` | Внутренняя учетка |
| **`gitbox`** | `192.168.1.132` | Git-сервер Forgejo | 3000 (HTTP), 22 (SSH) | `https://git.lan/` | Внутренняя учетка |
| **`matrixbox`** | `192.168.1.93` | Федеративный мессенджер Conduit | 6167 (HTTP) | `https://matrix.lan/` | Внутренняя учетка |
| **`mediabox`** | `192.168.1.150` | Музыка, Торренты, Samba, WSDD | 445 (SMB), 4533 (Navidrome), 9091 (Transmission) | `https://music.lan/`<br>`https://torrent.lan/` (-> `/web/`) | Samba: admin / пароль<br>Transmission: Basic Auth |
| **`ytdlbox`** | `192.168.1.155` | Загрузчик видео/аудио через прокси | 8080 (HTTP) | `https://ytdl.lan/` | Basic Auth |

---

## 3. Ключевые архитектурные решения

### sing-box 1.14 Smart RuNet Routing & Binary Rule-Sets (.srs)
- **Нативные скомпилированные бинарные базы `.srs`**: Вместо примитивных списков суффиксов используются официальные базы RunetFreedom и MetaCubeX в бинарном формате sing-box 1.14:
  - `geoip-ru.srs` (103.7 KB) — полный реестр всех российских автономных систем и CIDR подсетей IPv4.
  - `geosite-tld-ru.srs` (155 B) — все национальные зоны первого уровня (`.ru`, `.su`, `.рф`, `.moscow`, `.tatar`, `.дети` и др.).
  - `geosite-category-ru.srs` (8.5 KB) — обширный реестр российских сервисов на иностранных доменах (VK, Telegram RU CDN, Yandex, Ozon, WB, банки, госуслуги).
  - `geosite-ru-blocked.srs` (533 KB) — полный реестр ресурсов, блокируемых или замедляемых в РФ (YouTube, Discord, Instagram, X/Twitter, трекеры).
  - `geosite-category-ai.srs` (2.0 KB) — официальный мета-реестр зарубежных AI-платформ (OpenAI/ChatGPT/Sora, Anthropic/Claude, Google Gemini/AI Studio, Copilot, Perplexity, Midjourney, Mistral, Grok).
- **Многоконтурный DNS-резолвер**:
  - Домены AI-сервисов (`geosite-category-ai`) резолвятся через изолированный апстрим `ai-dns` (1.1.1.1 DoT с `detour: "🤖 Нейросети"`), что исключает CDN IP mismatch и гео-блокировки.
  - Заблокированные домены (`geosite-ru-blocked`) резолвятся через `remote-dns` (1.1.1.1 DoT с `detour: "🚀 Выбор узла"`).
  - Национальные и отечественные домены (`geosite-tld-ru`, `geosite-category-ru`) резолвятся локально через `77.88.8.8` (detour `direct`), что исключает утечки и подмену.
- **Интеллектуальная фильтрация нод и выделенные группы**:
  - Скрипт `update-sub.py` автоматически отфильтровывает информационные заглушки провайдеров (`127.0.0.1:1`, `ℹ️ Срок`, `⛔️ Торренты`, `📰 Новости`).
  - Группа `🌐 Автовыбор` (`urltest`) динамически агрегирует **все 200+ реальных серверов** с пингом через `generate_204`.
  - Отдельная группа `🤖 Нейросети` (`selector`) позволяет зафиксировать чистый узел или резидентный IP специально под строгий антифрод Claude/OpenAI/Gemini.
  - Отдельная группа `Torrent` по умолчанию направляет торрент-трафик в `direct`, защищая VPN-сервер от жалоб провайдеров (DMCA).
- **Универсальный парсер входящих прокси (Universal Ingestion Engine)**:
  - Поддерживает автоматический парсинг и конвертацию в нативный sing-box 1.14:
    - Готовых JSON-подписок sing-box.
    - Стандартных Base64-подписок V2Ray (списки ссылок).
    - Одиночных share-ссылок: `vless://` (с полной поддержкой Reality, Vision, gRPC, WebSocket), `ss://` (SIP002 Base64 и AEAD-2022), `trojan://` (TLS/uTLS), `hysteria2://` / `hy2://` (Salamander обфускация).
    - Режима `direct` (для чистой работы без внешнего VPN с возможностью подключения в любой момент).
  - Интерактивный мастер в начале скрипта с тайм-аутом 30 секунд и возможностью передачи параметров через CLI-аргумент или переменную `SUB_URL`.
- **Сетевая стабильность TUN**:
  - Для интерфейса `tun0` установлен `strict_route: false`, что предотвращает коллизии с L2-мостом `br0` и гарантирует доступность хоста по SSH.
  - Уровень логирования установлен в `info` для визуализации маршрутов в веб-панели Metacubexd.

### AdGuard Home 2026 RuNet Edition
- Шифрованный апстрим через DNS-over-HTTPS (`https://dns.yandex.ru/dns-query`, `https://cloudflare-dns.com/dns-query`) исключает подмену DNS провайдерским ТСПУ.
- Включен официальный российский фильтр (Filter ID 7) против отечественных трекеров и баннерных сетей.
- Включен `cache_optimistic: true` для моментальных ответов (< 1 мс).
- Отключен IPv6 DNS (`aaaa_disabled: true`) для устранения сетевых задержек.

### Оптимизация ядра Linux (BBR TCP Congestion)
- На хосте активирован современный алгоритм управления перегрузками **Google BBR** (`net.ipv4.tcp_congestion_control = bbr`) и планировщик `fq`, обеспечивающие стабильный высокий битрейт при стриминге 4K-видео и работе с прокси.

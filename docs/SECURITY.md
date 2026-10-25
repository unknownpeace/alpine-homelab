# Модель безопасности и харднинг (Zero-Docker Alpine Homelab)

1. **Принцип наименьших привилегий (Least Privilege)**: Все веб- и медиа-сервисы (Vaultwarden, Forgejo, Conduit, Transmission, Navidrome, Caddy) запускаются под изолированными непривилегированными системными учетными записями. Общий доступ к медиатеке ограничен правами группы `media` (`2775`).
2. **Cgroups v2 & LXC Sandbox Isolation**: Пользовательские песочницы изолированы через пространства имен ядра Linux и лимитируются по памяти и процессору через cgroups v2.
3. **Bcrypt HTTP Basic Auth**: Сервисы управления (LXC Manager, Metacubexd, Transmission RPC, yt-dlp) защищены обратным прокси Caddy с Bcrypt-хешированием паролей.
4. **Local PKI & HTTPS**: Автономный встроенный центр сертификации Caddy Local CA обеспечивает доверенное шифрование локальных доменов `*.lan` без передачи ключей во внешние сервисы. Директива `skip_install_trust` исключает модификацию доверенных корневых сертификатов хоста.
5. **Nftables Firewall & DNAT Forwarding**: Фильтрация пакетов и проброс портов осуществляются на уровне ядра Linux через нативный `nftables` с MSS-клампингом (`tcp flags syn tcp option maxseg size set rt mtu`).
6. **SMB3 Защищенный файловый доступ**: Samba 4 настроена по стандартам безопасности Windows 11 (24H2) с минимальным протоколом `SMB2_10`, автоматическим шифрованием и подписью (`server signing = auto`, `smb encrypt = auto`), а также блокировкой гостевого входа (`map to guest = Never`).

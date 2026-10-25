#!/usr/bin/env python3
import sys, os, subprocess, json, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

STORAGE_DIR = os.environ.get("STORAGE_BASE", "/srv/storage")

class SimpleYTDLHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Homelab Media Downloader</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 650px; margin: 40px auto; padding: 20px; background: #0f172a; color: #f8fafc; }
        .card { background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
        input[type=text] { width: 100%; box-sizing: border-box; padding: 12px; margin: 10px 0; border: 1px solid #334155; border-radius: 6px; background: #0f172a; color: #fff; }
        select { width: 100%; box-sizing: border-box; padding: 12px; margin: 10px 0; border: 1px solid #334155; border-radius: 6px; background: #0f172a; color: #fff; }
        button { background: #3b82f6; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; width: 100%; }
        button:hover { background: #2563eb; }
        .footer { margin-top: 15px; font-size: 0.85em; color: #94a3b8; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📥 Homelab Media Downloader</h2>
        <form method="POST" action="/download">
            <label>Ссылка на видео/аудио (YouTube, VK, Rutube, SoundCloud):</label>
            <input type="text" name="url" placeholder="https://..." required autofocus>
            <label>Папка назначения:</label>
            <select name="folder">
                <option value="downloads">Загрузки (/srv/storage/downloads)</option>
                <option value="music">Музыка (/srv/storage/music)</option>
            </select>
            <button type="submit">Скачать в хранилище</button>
        </form>
        <div class="footer">Файлы автоматически сохраняются с правами группы media (2775).</div>
    </div>
</body>
</html>"""
        self.wfile.write(html.encode('utf-8'))

    def do_POST(self):
        if self.path == '/download':
            length = int(self.headers.get('content-length', 0))
            post_data = self.rfile.read(length).decode('utf-8')
            params = parse_qs(post_data)
            url = params.get('url', [''])[0].strip()
            folder = params.get('folder', ['downloads'])[0]
            if folder not in ['downloads', 'music']:
                folder = 'downloads'

            if url:
                target_dir = os.path.join(STORAGE_DIR, folder)
                os.makedirs(target_dir, exist_ok=True)
                def run_dl(u, d):
                    cmd = [
                        "/usr/bin/yt-dlp",
                        "-P", d,
                        "-o", "%(title)s.%(ext)s",
                        "--no-playlist",
                        u
                    ]
                    subprocess.run(cmd)
                threading.Thread(target=run_dl, args=(url, target_dir)).start()

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            resp = """<!DOCTYPE html><html><head><meta http-equiv="refresh" content="2;url=/"/><style>body{background:#0f172a;color:#f8fafc;font-family:sans-serif;text-align:center;padding-top:50px;}</style></head><body><h3>🚀 Загрузка запущена в фоновом режиме!</h3><p>Возврат назад...</p></body></html>"""
            self.wfile.write(resp.encode('utf-8'))

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 8080), SimpleYTDLHandler)
    server.serve_forever()

# Autonomous Content Archiving Pipeline

> Production-grade YouTube channel archiver built on yt-dlp — structured output, retries, incremental backups.

A **production-grade** YouTube channel archiver using yt-dlp.

---

## 🚀 Production Features

| Feature | Description |
|---------|-------------|
| **Structured Directories** | `/ChannelName/Year/VideoTitle/` organization |
| **Retry Logic** | Exponential backoff: 3 attempts (5s → 10s between attempts) |
| **Incremental Backups** | `archive.log` skips already-downloaded videos |
| **Metadata Extraction** | `.info.json`, `.description`, thumbnails |
| **Dual Logging** | `pipeline.log` + `error.log` |

---

## Prerequisites

These are **system dependencies** — they are not Python packages and are not installed by `requirements.txt`:

| Dependency | Why |
|------------|-----|
| **Python 3.10+** | Runtime for the archiver |
| **Node.js** (or **Deno**) | JavaScript runtime required by yt-dlp to solve YouTube's JS challenge. Node is preferred when both are present (see `downloader.py`). Without either, some formats/streams may fail to resolve |
| **ffmpeg** | Merges `bestvideo` + `bestaudio` into a single `.mp4` (per the configured `format` / `merge_output_format`) |

Verify:

```bash
python3 --version
node --version    # or: deno --version
ffmpeg -version
```

---

## First-Time Setup

```bash
git clone https://github.com/neo-polus9/-youtube-channel-archiver.git
cd ./-youtube-channel-archiver   # leading dash: prefix with ./ or cd misreads it as an option
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

Point `cd` at wherever you cloned the repo (example path):

```bash
cd ~/path/to/-youtube-channel-archiver   # example — use your actual clone location
source venv/bin/activate
python3 downloader.py
```

Enter the channel URL when prompted.

---

## Output Structure

```
downloads/
└── ChannelName/
    └── 2024/
        └── Video Title/
            ├── Video Title.mp4
            ├── Video Title.info.json
            ├── Video Title.description
            └── Video Title.jpg
```

`downloads/`, `venv/`, logs (`*.log`), `archive.log`, and `failed_downloads_*.json` are local state and are excluded from the repository via `.gitignore`.

---

## Troubleshooting

**403 Errors:** Upgrade yt-dlp:
```bash
pip install -U yt-dlp
```

**`ModuleNotFoundError: No module named 'yt_dlp.extractor.<something>'`:** the yt-dlp install is corrupted (files deleted, e.g. by an over-eager antivirus/content filter). Repair it:
```bash
python3 -m pip install --force-reinstall --no-deps yt-dlp
```

**`failed_downloads_*.json` report:** with `ignoreerrors` enabled, yt-dlp skips broken/unavailable videos instead of aborting the whole channel. Those failures are collected and written to `failed_downloads_<timestamp>.json` (with per-video error messages) instead of silently reporting success.

**Hangs at `[youtube:tab] Downloading webpage`:** usually a broken or filtered IPv6 route. Current yt-dlp versions ignore the old `force_ipv4` option; this project uses `source_address: 0.0.0.0` to force IPv4.

**Skipped EJS challenge solver / missing formats:** install the companion package as part of the default dependency set:
```bash
python3 -m pip install -U 'yt-dlp[default]'
```
This project declares `yt-dlp[default]` in `requirements.txt`; it avoids enabling runtime downloads of remote challenge-solver scripts from GitHub. Also make sure Node.js or Deno is on your `PATH` (see [Prerequisites](#prerequisites)) — the solver scripts still need a JS runtime to execute.

---

## Portfolio Summary

This pipeline demonstrates:
- **API Integration** - yt-dlp library for YouTube data extraction
- **Error Resilience** - Exponential backoff retry patterns
- **Data Organization** - Structured file system architecture
- **Incremental Processing** - Efficient delta synchronization
- **Production Logging** - Audit trails and error tracking

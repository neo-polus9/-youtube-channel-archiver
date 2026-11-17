#!/usr/bin/env python3
"""
Autonomous Content Archiving Pipeline
======================================
A production-grade YouTube channel archiver using yt-dlp.

Features:
- Structured directory organization: /ChannelName/Year/VideoTitle/
- Retry logic with exponential backoff (up to 3 attempts)
- Incremental backups via archive.log
- Metadata extraction to .json files
- Comprehensive logging
"""

import yt_dlp
import os
import re
import shutil
import time
import random
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


# ============================================================================
# CONFIGURATION
# Anchor state files to the script directory so running from a different
# CWD can't silently create a fresh archive.log / duplicate downloads.
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = str(BASE_DIR / "downloads")
ARCHIVE_FILE = str(BASE_DIR / "archive.log")
LOG_FILE = str(BASE_DIR / "pipeline.log")
ERROR_LOG_FILE = str(BASE_DIR / "error.log")

MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 5  # seconds

# Node is preferred because it is installed here; the Python API does not
# read the yt-dlp CLI user config (~/.config/yt-dlp/config), so this option
# must be supplied explicitly. Fall back to yt-dlp's default (deno).
NODE_EXE = shutil.which('node')
JS_RUNTIMES = {'node': {'path': NODE_EXE}} if NODE_EXE else {'deno': {}}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging():
    """Configure dual logging: file + console."""
    
    # Main logger
    logger = logging.getLogger('ArchivePipeline')
    if logger.handlers:
        # Already configured - avoid duplicate handlers on repeated instantiation
        return logger
    logger.setLevel(logging.DEBUG)
    
    # File handler - detailed logs
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    # Console handler - info and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('[%(levelname)s] %(message)s')
    console_handler.setFormatter(console_format)
    
    # Error file handler
    error_handler = logging.FileHandler(ERROR_LOG_FILE, encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_format)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.addHandler(error_handler)
    
    return logger


# ============================================================================
# ARCHIVING PIPELINE
# ============================================================================

class _ErrorCollector:
    """yt-dlp logger adapter: captures error lines per download attempt."""

    def __init__(self, log):
        self.messages = []
        self._log = log

    def debug(self, msg):
        msg = str(msg)
        # Progress updates are reported by _progress_hook at coarse intervals;
        # don't flood pipeline.log with one line per read chunk.
        if msg.startswith('[download]'):
            return
        self._log.debug(msg)

    def info(self, msg):
        pass  # progress is handled by hooks

    def warning(self, msg):
        self._log.warning(msg)

    def error(self, msg):
        self._log.error(msg)
        self.messages.append(str(msg))


class ContentArchiver:
    """Production-grade YouTube channel archiver."""

    def __init__(self, output_dir=OUTPUT_DIR):
        self.output_dir = output_dir
        self.logger = setup_logging()
        self.failed_downloads = []
        self._error_collector = _ErrorCollector(self.logger)
        self._progress_marks = {}
        
        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        self.logger.info("=" * 60)
        self.logger.info("AUTONOMOUS CONTENT ARCHIVING PIPELINE")
        self.logger.info("=" * 60)
    
    def get_ydl_options(self):
        """Build yt-dlp options with production settings."""
        return {
            # Format: Best quality with ffmpeg merge
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            
            # Structured output: /Channel/Year/VideoTitle/VideoTitle.ext
            'outtmpl': os.path.join(
                self.output_dir,
                '%(uploader)s',
                '%(upload_date>%Y)s',
                '%(title)s',
                '%(title)s.%(ext)s'
            ),
            
            # Incremental backup - skip already downloaded
            'download_archive': ARCHIVE_FILE,
            
            # Metadata extraction
            'writeinfojson': True,
            'writedescription': True,
            'writethumbnail': True,
            
            # Error handling.
            # NOTE: with ignoreerrors=True, yt-dlp never raises DownloadError;
            # failures are reported via the custom logger and a non-zero
            # retcode from ydl.download(). The retry logic below checks both.
            'ignoreerrors': True,
            'no_warnings': False,
            'logger': self._error_collector,
            
            # Anti-throttling measures
            'user_agent': random.choice(USER_AGENTS),
            'sleep_interval': 5,
            'max_sleep_interval': 15,
            'sleep_interval_requests': 1,
            # Make all connections via IPv4. yt-dlp >=2025 uses source_address
            # for this; the old 'force_ipv4' key is silently ignored, which
            # lets connections fall back to a broken/filtered IPv6 route.
            'source_address': '0.0.0.0',

            # JavaScript challenge solving: enable Node when available.
            'js_runtimes': JS_RUNTIMES,
            
            # Progress hooks
            'progress_hooks': [self._progress_hook],
            'postprocessor_hooks': [self._postprocessor_hook],
            
            # Quiet mode off for summary output; noprogress prevents one log
            # line per downloader read/write chunk.
            'quiet': False,
            'verbose': False,
            'noprogress': True,
        }
    
    def _progress_hook(self, d):
        """Track download progress."""
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes') or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if not total:
                return

            filename = d.get('filename') or d.get('tmpfilename') or 'Unknown'
            key = filename
            percent = downloaded * 100 / total

            for mark in (25, 50, 75, 90):
                if percent >= mark and self._progress_marks.get(key, -1) < mark:
                    self.logger.info(f"… {mark}% of {os.path.basename(filename)}")
                    self._progress_marks[key] = mark
        elif d['status'] == 'finished':
            filename = d.get('filename', 'Unknown')
            self.logger.info(f"✓ Downloaded: {os.path.basename(filename)}")
        elif d['status'] == 'error':
            filename = d.get('filename', 'Unknown')
            self.logger.error(f"✗ Download failed: {filename}")
            self._error_collector.messages.append(f"{filename}: download error reported by progress hook")
    
    def _postprocessor_hook(self, d):
        """Track post-processing (merging, etc.)."""
        if d['status'] == 'finished':
            self.logger.debug(f"Post-processing complete: {d.get('postprocessor')}")
    
    def download_with_retry(self, url):
        """
        Download with exponential backoff retry logic.

        Up to MAX_RETRIES attempts; delays 5s → 10s between attempts.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            self._error_collector.messages.clear()
            try:
                self.logger.info(f"Attempt {attempt}/{MAX_RETRIES}: {url}")

                opts = self.get_ydl_options()
                with yt_dlp.YoutubeDL(opts) as ydl:
                    retcode = ydl.download([url])

                if retcode == 0:
                    self.logger.info("Download completed successfully.")
                    return True

                # yt-dlp finished but reported errors (ignoreerrors=True swallows
                # DownloadError, so the retcode is the source of truth)
                errors = list(self._error_collector.messages)
                detail = errors[-1] if errors else f"yt-dlp exited with code {retcode}"
                self.logger.warning(f"Attempt {attempt} finished with errors: {detail}")

                if attempt < MAX_RETRIES:
                    delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
                    self.logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                    continue

                self.logger.error(f"Failed after {MAX_RETRIES} attempts: {url}")
                self.failed_downloads.append({
                    'url': url,
                    'error': detail,
                    'errors': errors,
                    'timestamp': datetime.now().isoformat()
                })
                return False

            except yt_dlp.utils.DownloadError as e:
                delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
                self.logger.warning(f"Download error: {e}")
                
                if attempt < MAX_RETRIES:
                    self.logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    self.logger.error(f"Failed after {MAX_RETRIES} attempts: {url}")
                    self.failed_downloads.append({
                        'url': url,
                        'error': str(e),
                        'timestamp': datetime.now().isoformat()
                    })
                    return False
                    
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                self.failed_downloads.append({
                    'url': url,
                    'error': str(e),
                    'timestamp': datetime.now().isoformat()
                })
                return False
        
        return False
    
    def archive_channel(self, channel_url):
        """
        Archive all videos from a YouTube channel.
        
        Uses incremental backup to skip already-downloaded videos.
        """
        self.logger.info("-" * 60)
        self.logger.info(f"TARGET: {channel_url}")
        self.logger.info("-" * 60)
        
        start_time = datetime.now()
        success = self.download_with_retry(channel_url)
        end_time = datetime.now()
        
        # Generate summary
        duration = end_time - start_time
        self.logger.info("=" * 60)
        self.logger.info("ARCHIVE SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info(f"Duration: {duration}")
        self.logger.info(f"Archive file: {ARCHIVE_FILE}")
        self.logger.info(f"Output directory: {self.output_dir}")
        
        if self.failed_downloads:
            self.logger.warning(f"Failed downloads: {len(self.failed_downloads)}")
            self._save_failed_report()
        else:
            self.logger.info("All downloads completed successfully!")
        
        return success
    
    def _save_failed_report(self):
        """Save failed downloads to a JSON report."""
        report_file = str(BASE_DIR / f"failed_downloads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(self.failed_downloads, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Failed downloads report: {report_file}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

# ANSI / terminal control sequences (e.g. bracketed-paste markers like
# \x1b[201~) get pasted into the URL when copying from a terminal
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
CTRL_RE = re.compile(r'[\x00-\x1f\x7f]')


def sanitize_url(raw):
    """Strip ANSI/control sequences from pasted input and validate the URL."""
    url = CTRL_RE.sub('', ANSI_RE.sub('', raw)).strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return url
    return None


def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║     AUTONOMOUS CONTENT ARCHIVING PIPELINE v1.0               ║
║     Production-Grade YouTube Channel Archiver                ║
╠══════════════════════════════════════════════════════════════╣
║  Features:                                                   ║
║  • Structured directories: /Channel/Year/VideoTitle/         ║
║  • Retry logic with exponential backoff                      ║
║  • Incremental backups (archive.log)                         ║
║  • Metadata extraction (.json, .description, .jpg)           ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    raw_url = input("Enter YouTube Channel URL: ")
    url = sanitize_url(raw_url)

    if not url:
        print("[ERROR] No valid URL provided (expected an http(s) URL).")
        return
    
    archiver = ContentArchiver()
    archiver.archive_channel(url)


if __name__ == "__main__":
    main()

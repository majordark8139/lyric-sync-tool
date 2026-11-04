# Lyric Sync Tool

> [!CAUTION]
> ### CAUTION: EARLY ALPHA
> **Lyric Sync Tool is currently in experimental early alpha development.**
>
> - **Backup Your Files First:** Always make a full backup of your music collection before running batch tagging or deletion operations. While tagging operations are written defensively, audio file metadata modifications are performed in-place.
> - **Upstream Lyric APIs Can Break or Throttle:** The synchronized lyrics providers rely on third-party remote endpoints (including Unison, LRCRed, and Kugou). Upstream services may change schemas, introduce rate limits, or suffer downtime at any time without notice.
> - **Use at your own discretion.**

---

Lyric Sync Tool is a cross-platform desktop application designed to discover, match, verify, and embed word-level and syllable-level synchronized lyrics (`.lrc`, `.elrc`, `.ttml`) for audio files.

---

## Features

- **Word & Syllable-Level Synchronization**: Strict word-sync enforcement, supporting Apple Music style syllable-timed lyrics (`.ttml`), Enhanced LRC (`.elrc`), and line-synced fallback (`.lrc`).
- **Multi-Provider Pipeline**: Searches multiple upstream providers with candidate scoring, duration verification, strict title-similarity floors, and an intelligent collaboration query ladder.
- **Background Vocal & Ad-Lib Separation**: Automatically isolates parenthetical ad-libs and background vocals into dedicated streams (`ttm:role="x-bg"` in TTML) without corrupting lead vocal timing.
- **Comprehensive Audio Tagging**:
  - **FLAC**: Vorbis comments (`LYRICS` block)
  - **MP3**: ID3v2.3/v2.4 (`USLT`, `TXXX:LYRICS`, `SYLT`)
  - **MP4 / M4A**: QuickTime `©lyr` atom
  - **Sidecars**: Exports companion `.ttml`, `.elrc`, and `.lrc` files
- **Granular Sync State Detection**: Inspects existing tags and sidecars to categorize tracks as `WORD` (word-synced), `LINE` (line-synced), or `NONE` (unsynced/missing).
- **Batch Processing Worker**: Multi-threaded scanner and tagger with live progress indicators, cancellation controls, and smart filtering (e.g. *Select Missing Word Sync Only*).
- **Synchronized Audio Preview Player**: Embedded media player with seekbar and active lyric line/syllable highlighting for quick manual verification.
- **Manual Search & Candidate Inspector**: Search across providers with custom keyword queries, review fuzzy matching scores, inspect lyrics lines, and manually apply candidate lyrics.
- **Granular Lyrics Cleanup**: Remove existing embedded tags or sidecars (`.lrc`, `.elrc`, `.ttml`) with optional `.bak` backup creation.
- **Modern PySide6 GUI**: Clean interface with Dark/Light theme toggle, persistent tree view column widths, and remembered panel splitters.

---

## Prerequisites

- **Python**: 3.10 or newer (64-bit)
- **Operating System**:
  - **Linux**: 64-bit desktop distribution (Ubuntu 22.04+, Linux Mint 21+, Debian 12+, Fedora 38+, Arch Linux, etc.)
  - **Windows**: 64-bit Windows 10 or Windows 11
- **Network**: Active internet connection to query lyric providers.

---

## Installation & Plug-and-Play Quickstart

### Linux

#### Option 1: Instant Plug-and-Play Launch
Clone the repository and run `run.sh`. If the virtual environment does not exist yet, it will automatically bootstrap `.venv`, install all requirements, configure the library paths, and start the app:

```bash
git clone https://github.com/majordark8139/lyric-sync-tool.git
cd lyric-sync-tool
chmod +x run.sh install.sh
./run.sh
```

#### Option 2: Full System & Desktop Menu Integration
Run `install.sh` to install desktop shortcuts and verify Qt6 platform dependencies:

```bash
./install.sh
```

This will:
1. Verify Qt6 XCB platform dependencies (such as `libxcb-cursor`).
2. Create an isolated `.venv` virtual environment in the project directory.
3. Install required Python packages (`PySide6`, `mutagen`, `requests`).
4. Generate an application menu entry (`~/.local/share/applications/lyric-sync-tool.desktop`).

---

### Windows

1. Clone or download the repository:
   ```cmd
   git clone https://github.com/majordark8139/lyric-sync-tool.git
   cd lyric-sync-tool
   ```
2. Double-click **`run.bat`** (or execute `run.bat` in Command Prompt / PowerShell).
3. On first run, `run.bat` will automatically:
   - Detect Python 3.10+ in your PATH.
   - Create a local `.venv` virtual environment.
   - Upgrade `pip` and install `requirements.txt`.
   - Launch the GUI application.

Subsequent launches with `run.bat` open the application instantly.

---

## Zero-Debris Uninstall

Lyric Sync Tool is completely self-contained within its folder and isolated virtual environment. To remove it completely without leaving background daemons or debris on your machine:

### Linux
1. Remove the project directory:
   ```bash
   rm -rf ~/Documents/lyric-sync-tool
   ```
2. *(Optional)* Remove the desktop launcher and local application settings:
   ```bash
   rm -f ~/.local/share/applications/lyric-sync-tool.desktop
   rm -rf ~/.config/lyric-sync-tool
   ```

### Windows
1. Delete the `lyric-sync-tool` directory.
2. *(Optional)* Remove saved window geometry and UI preferences from the Windows Registry:
   ```cmd
   reg delete "HKCU\Software\lyric-sync-tool" /f
   ```

No system services, background processes, or system-wide configuration files are ever left behind.

---

## Running Automated Tests

To run the full unit test suite (180+ tests covering providers, candidate scoring, TTML serialization, LRC parsing, audio taggers, and GUI):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

---

## Acknowledgements

Special thanks to **Gemini** and **Claude** for:
- Core architectural design and multi-provider synchronization pipelines.
- Low-level CPython and Shiboken reference count underflow diagnostics and remediation.
- glibc memory safety and thread garbage-collection stability patches.
- Comprehensive test suite automation and validation across providers and UI layers.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.


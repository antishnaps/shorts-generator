# Running from source and development

The Windows ZIP includes Python, FFmpeg/ffprobe and Deno. These instructions are
only for the separate source package or a Git clone, not for EXE users.

## Set up

Install Python 3.12 x64, extract the source package into a writable folder and
run `start.bat`. It creates a local virtual environment and installs from PyPI.
Alternatively:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Check startup with `main.py --smoke-test`. The source package uses
imageio-ffmpeg as a fallback; install full FFmpeg/ffprobe for features that need
them. YouTube downloads also require a supported JavaScript runtime such as
Deno. Fonts and Vosk recognition models are not bundled with the source.

## Tests and builds

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Offline tests cover subtitles, media-source clients, download policy, settings,
free-edition startup and the donation interface. They do not verify live API
availability or end-to-end online generation. See [portable build instructions](../SOURCE_ACCESS.md).

After changing the receiving wallet address, regenerate the QR with
`python tools/generate_support_qr.py`. Tests verify its payload and readability
at the actual dialog size.

## Privacy and licenses

Keep keys, personal configs, cookies, OAuth tokens and logs out of Git.
The defaults contain no developer credentials; commercial activation,
sales infrastructure and the experimental montage lab are excluded.
YouTube publishing requires your own OAuth client and channel authorization.
Content sent to external APIs is handled under their providers' terms.

Application source is GPL-3.0-only. Dependencies have separate terms; see
[notices](../THIRD_PARTY_NOTICES.md). Portable archives contain dependency versions,
license texts and source access details. Audit those obligations when distributing
your own binaries. No warranty, media rights, audience or revenue guarantees are provided.

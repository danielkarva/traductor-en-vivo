# AGENTS.md — instructions for AI coding assistants

You are helping a (probably non-technical, Spanish-speaking) user install and configure **Traductor en vivo**: a Windows-only floating window that captures system audio (WASAPI loopback), transcribes it with faster-whisper and shows a live translation (Google Translate). Optional: Discord local RPC to label who is speaking.

**Talk to the user in Spanish, one step at a time.** Do everything you can yourself; only ask the user for the steps that need their hands or their Discord account.

## 1. Check prerequisites
- Windows 10/11 only. If not Windows, stop and explain it is not supported.
- Python 3.10–3.13: try `py -3.12 --version`, `py -3.11 --version`, `python --version`. If none, ask the user to install Python 3.12 from python.org **with "Add python.exe to PATH" checked**, then continue.
- NVIDIA GPU (optional): `nvidia-smi`. Without it everything runs on CPU (slower, smaller model).

## 2. Install
Preferred: run `instalar.bat` from the project folder (non-interactive except the final `pause`). It:
1. creates `.venv` with the first Python 3.10–3.13 found;
2. `pip install -r requirements.txt`;
3. `pip install -r requirements-nvidia.txt` only if `nvidia-smi` exists (CUDA 12 cuBLAS + cuDNN 9 wheels, ~2 GB);
4. copies `config.example.json` → `config.json` if missing;
5. creates a desktop shortcut to `.venv\Scripts\pythonw.exe traductor.py`.

Manual equivalent (if the .bat cannot be run):
```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -r requirements-nvidia.txt   # only with NVIDIA
copy config.example.json config.json
```

### If pip fails with `CERTIFICATE_VERIFY_FAILED`
An antivirus is intercepting HTTPS (Avast/AVG/Kaspersky/ESET…). Build `ca-bundle.pem` in the project root = Python's certifi bundle + the antivirus root certificate, then rerun with `--cert ca-bundle.pem` (`instalar.bat` and `traductor.py` pick it up automatically). For Avast the root cert is usually at `C:\ProgramData\Avast Software\Avast\wscert.pem`; for others, export it from the Windows certificate store (`certmgr.msc` → Trusted Root → the antivirus cert → Base-64 .cer). Example:
```
.venv\Scripts\python.exe -c "import pip._vendor.certifi as c; print(c.where())"
# concatenate that file + the antivirus .pem/.cer into ca-bundle.pem
```
`ca-bundle.pem` is gitignored.

## 3. Verify
- Launch: `Traductor.bat` (or the desktop shortcut). It runs with `pythonw` (no console). **All errors go to `traductor.log`** in the project folder — read it to debug.
- First launch downloads Whisper models into `modelos/` (1.5–4.5 GB). The status bar shows progress text.
- Quick headless check of the model choice:
  `.venv\Scripts\python.exe -c "import traductor as T; print(T.gpu_info(), T.pick_models(T.load_config()))"`
- To test end to end, play any English video: lines must appear in the window within ~1–2 s after each sentence.

## 4. Optional: Discord speaker names
The user must create **their own** Discord application (never reuse someone else's credentials):
1. https://discord.com/developers/applications → **New Application** → name it → Create.
2. **OAuth2** tab → copy **Client ID**; **Reset Secret** → copy **Client Secret**; **Redirects** → add `http://localhost` → **Save Changes**.
3. Write both into `config.json` as `discord_client_id` / `discord_client_secret` (keep the JSON valid, **UTF-8 without BOM** — PowerShell 5.1 `Set-Content -Encoding utf8` adds a BOM and breaks loading; write it with Python instead).
4. Restart the translator; the Discord **desktop app** (not web) shows an authorize popup → user clicks **Authorize**. The token is stored in `discord_token.json`.
Treat the secret as a secret: do not print it back in full, do not commit it.

## 5. Things you must NOT do
- Do not commit or upload `config.json`, `discord_token.json`, `ca-bundle.pem`, `.venv/`, `modelos/`, logs (all in `.gitignore`).
- Do not uninstall `onnxruntime`: faster-whisper needs it for its VAD filter (without it nothing gets transcribed and the only symptom is an error in `traductor.log`).
- Do not add a local LLM (Ollama etc.) on the same GPU as Whisper: on an 8 GB RTX 3070 it hung the NVIDIA driver and forced a reboot; on CPU it used ~90 % CPU. Translation stays on Google.
- Do not add features that save conversation text or audio to disk; the project promises not to.

## Project map
| File | What |
|---|---|
| `traductor.py` | Everything: audio capture, segmentation, Whisper, translation, Tk window |
| `discord_speakers.py` | Discord local RPC (named pipe `discord-ipc-N`), OAuth, who-spoke-when, per-word attribution, lag auto-calibration |
| `config.example.json` | Template for `config.json` (all keys and defaults are in `DEFAULT_CONFIG` in `traductor.py`) |
| `instalar.bat` / `Traductor.bat` | Installer / launcher |
| `requirements*.txt` | Dependencies (NVIDIA ones separate) |

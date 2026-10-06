# TeraLink

Tera Term / RDP quick-login tool for Windows, in two editions:

- `src/` – original C# / WinForms (.NET 10). Build and check with `build.ps1` and `dotnet run --project tests/TeraLink.Checks -c Release` (Windows only).
- `python/` – Python 3.8+ / tkinter edition for PCs that block the exe; adds WAR build and SFTP deploy tasks. Started from `TeraLink.bat`.

Before working on the Python edition, read `python/ROADMAP.md` (status, untested parts, Windows acceptance checklist, planned refactor).

## Python edition conventions

- Standard library only at runtime; paramiko is optional (`remote.py` falls back to Windows OpenSSH).
- Keep Python 3.8 syntax (`from __future__ import annotations`, `typing.List`, no `match`).
- Never put passwords in process arguments, files, logs or the clipboard; keep the named-pipe + PID check for Tera Term.
- UI text is Chinese. `.bat` files are ASCII with CRLF (enforced by `.gitattributes`).
- Tests: `cd python && python -m unittest discover -s tests` – must pass without Windows, tkinter or paramiko.

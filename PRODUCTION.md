# pipedbg Desktop & Production Build Guide

This guide covers building and deploying pipedbg as a native desktop application using Electron, plus production optimizations and mobile support.

## Phase 3.5: Electron Desktop Wrapper + Production Refinements

### Architecture

```
pipedbg/
├── electron/
│   ├── main.js                 # Electron main process
│   ├── preload.js              # Security context bridge
│   └── entitlements.mac.plist  # macOS security entitlements
├── web/
│   ├── index.html              # Mobile-responsive UI
│   ├── styles.css              # Dark theme + responsive breakpoints
│   └── app.js                  # DAG rendering + error handling
├── pipedbg/
│   ├── webui.py                # Enhanced with error handling
│   ├── webui_server.py         # Standalone server wrapper
│   ├── cli.py                  # Multi-platform support
│   ├── parser.py               # GitHub/GitLab/CircleCI support
│   └── ...
├── package.json                # Electron + build config
└── electron-builder.yml        # Multi-platform build settings
```

### Features Implemented

#### 1. **Electron Wrapper**
- Native macOS/Windows/Linux application
- Spawns Python backend as subprocess
- Secure context isolation via preload.js
- Auto-opens UI in default browser
- Clean app lifecycle management

#### 2. **Production Error Handling**
- Try-catch wrapping on all API endpoints
- Path traversal security validation
- JSON parsing error recovery
- Job execution error capture with timeline logging
- Plan gating error responses (402 for rate limits)

#### 3. **Enhanced Frontend**
- Error notification panel with auto-dismiss
- Loading spinner during pipeline runs
- Graceful fallback for missing Clipboard API
- Aria labels for accessibility
- Responsive breakpoints: 1600px / 900px / 768px / 600px / 480px

#### 4. **DAG Virtualization**
- Limits timeline display to last 50 items
- Caps step rendering (50 per job) with overflow notice
- DocumentFragment batching for DOM operations
- Efficient event delegation
- Handles large workflows without UI lag

#### 5. **Mobile UI**
- Stacked layout on tablets/phones
- Touch-friendly button targets (min 44x44px)
- Safe area insets for notched phones
- Readable font sizes across all breakpoints
- Horizontal scrolling for DAG on narrow screens

---

## Building & Running

### Prerequisites

```bash
# Install Node.js and npm (required for Electron build)
node --version  # v16+ recommended
npm --version   # v7+

# pipedbg Python package already installed in venv
source pipedbg/.venv/bin/activate
```

### Option 1: Web UI (Development)

```bash
cd /Users/tayyab/Projects
source pipedbg/.venv/bin/activate
python -m pipedbg.cli ui pipedbg/python-ci.yml --host 127.0.0.1 --port 8765
```

Opens UI at `http://127.0.0.1:8765/?session=<id>`

### Option 2: Electron Development

```bash
cd /Users/tayyab/Projects/pipedbg

# Install Node dependencies
npm install

# Run in development mode (opens DevTools)
npm run dev
```

The Electron app will:
1. Spawn Python UI server (pipedbg.webui_server)
2. Open native window pointing to `http://127.0.0.1:8765`
3. Show DevTools for debugging
4. Close server on app exit

### Option 3: Build Installers

```bash
# macOS (DMG + ZIP)
npm run build:mac

# Windows (NSIS + Portable)
npm run build:win

# Linux (AppImage + DEB)
npm run build:linux

# All platforms
npm run build:all
```

Built installers appear in `dist/`:
- `pipedbg-3.0.0.dmg` (macOS)
- `pipedbg Setup 3.0.0.exe` (Windows installer)
- `pipedbg-3.0.0-portable.exe` (Windows standalone)
- `pipedbg-3.0.0.AppImage` (Linux)
- `pipedbg_3.0.0_amd64.deb` (Linux package)

---

## API Endpoints (Enhanced with Error Handling)

### `GET /api/state`
**Response:**
```json
{
  "session_id": "abc123def456",
  "share_url": "http://127.0.0.1:8765/?session=abc123def456",
  "workflow": { ...workflow DAG... },
  "breakpoints": ["step-1", "step-3"],
  "timeline": [
    { "timestamp": "2026-05-04T10:30:00Z", "job": "test", "step": "run-tests", "status": "SUCCESS", "exit_code": 0 }
  ],
  "plan": { "tier": "team", "ai_limit": null, "ai_calls_used": 0 }
}
```

**Errors:**
- `500` - State loading failed (see error message)

### `POST /api/breakpoint`
**Request:**
```json
{
  "session_id": "abc123def456",
  "step_id": "step-1",
  "enabled": true
}
```

**Errors:**
- `400` - Missing step_id
- `500` - Toggle operation failed

### `POST /api/run`
**Request:**
```json
{
  "session_id": "abc123def456",
  "no_docker": true,
  "dry_run": false
}
```

**Errors:**
- `500` - Pipeline execution failed (reason in error message)
- Timeline appended with ERROR status on per-job failures

**Enhanced Timeline on Error:**
```json
{
  "timestamp": "2026-05-04T10:30:00Z",
  "job": "job-id",
  "step": "error",
  "status": "ERROR",
  "error": "Docker not available"
}
```

### `POST /api/explain`
**Request:**
```json
{
  "session_id": "abc123def456",
  "failed_step": {
    "name": "Run tests",
    "id": "step-5",
    "exit_code": 1,
    "logs": ["Error: tests failed"]
  }
}
```

**Errors:**
- `402` - AI calls exceeded (free tier: 5 calls) → message suggests creating `.pipedbg-team`
- `500` - AI explain failed (API error)

---

## Security Considerations

### Preload Bridge (Electron)
- No `require()` or `process` access in renderer
- IPC communication via `electronAPI` object
- Clipboard access sanitized
- Prevents XSS from UI code

### Path Traversal Protection
```python
# Validated in webui.py _serve_static()
if ".." in filename or filename.startswith("/"):
    return 403 Forbidden
```

### JSON Parsing
```python
try:
    data = json.loads(raw)
except JSONDecodeError:
    return 400 Bad Request
```

---

## Performance Optimizations

### Frontend (DAG Virtualization)
- Steps capped at 50 per job (shows overflow notice)
- Timeline limited to last 50 events
- DocumentFragment batching for DOM
- Minimal event listeners via delegation

### Backend (Error Resilience)
- Per-job try-catch: failures don't crash server
- Graceful degradation (continue to next job)
- Timeline captures all errors for debugging
- No blocking on external services

---

## Mobile Testing

### Responsive Breakpoints
| Breakpoint | Layout | Use Case |
|-----------|--------|----------|
| 1600px+ | 3fr DAG / 1.5fr side | Large desktop |
| 1200px | 2fr DAG / 1fr side | Desktop |
| 900px | 1fr stacked | Tablet landscape |
| 768px | Full width stacked | Tablet portrait |
| 600px | Compact spacing | Large phone |
| 480px | Minimal spacing | Small phone |

### Test Commands
```bash
# Chrome DevTools
F12 → Ctrl+Shift+M (toggle device toolbar)

# Safari (macOS)
Develop → Enter Responsive Design Mode

# Firefox
Ctrl+Shift+M

# Real device
Visit http://<local-ip>:8765 from phone on same network
```

---

## Configuration

### Team Mode (Unlimited AI Calls)
```bash
# Create team marker file in repo root
touch /Users/tayyab/Projects/pipedbg/.pipedbg-team

# Or via CLI
cd /Users/tayyab/Projects/pipedbg
echo "team-mode" > ../.pipedbg-team
```

### Electron Builder Config
Edit `electron-builder.yml`:
```yaml
appId: com.pipedbg.app
productName: pipedbg
directories:
  buildResources: assets  # App icon directory
```

Icons needed:
- `assets/icon.icns` (macOS)
- `assets/icon.ico` (Windows)
- `assets/icon.png` (Linux)

---

## Troubleshooting

### Python Server Fails to Start
```
Error: Server startup timeout
```
**Solution:**
```bash
# Ensure venv is activated
source pipedbg/.venv/bin/activate

# Check port is free
lsof -i :8765

# Use different port
npm run dev -- --port 8766
```

### "No module named pipedbg"
**Solution:**
```bash
cd /Users/tayyab/Projects  # Parent directory, NOT pipedbg/
npm run dev
```

### Electron Build Fails
```bash
# Clear build cache
rm -rf dist node_modules package-lock.json
npm install
npm run build:mac
```

### Mobile UI Looks Broken
- Clear browser cache (Cmd+Shift+R)
- Open in Chrome DevTools responsive mode
- Check viewport meta tag is present in `index.html`

---

## Next Steps

### Future Enhancements
- [ ] Electron auto-updater for app distribution
- [ ] WebSocket for real-time timeline updates
- [ ] Session persistence (save/restore breakpoints)
- [ ] Dark/light theme toggle
- [ ] Keyboard shortcuts (Ctrl+R to run, Ctrl+K to clear timeline)
- [ ] Export timeline as JSON/CSV
- [ ] Collaborative debugging (WebRTC audio/video)
- [ ] CI/CD integration (GitHub Actions marketplace)

### Release Checklist
- [ ] Update version in `package.json` and `pyproject.toml`
- [ ] Build all platforms (`npm run build:all`)
- [ ] Test installers on target OS
- [ ] Sign binaries (codesign on macOS, certificate on Windows)
- [ ] Create GitHub release with installer downloads
- [ ] Update app via electron auto-updater

---

## References

- [Electron Documentation](https://www.electronjs.org/docs)
- [electron-builder](https://www.electron.build/)
- [MDN: Responsive Design](https://developer.mozilla.org/en-US/docs/Learn/CSS/CSS_layout/Responsive_Design)
- [Web Vitals (Performance)](https://web.dev/vitals/)

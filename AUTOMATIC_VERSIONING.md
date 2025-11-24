# Automatic Versioning & Reload System

This document explains how the automatic version detection and browser reload system works.

## Overview

The application uses git commit SHA as the version identifier, ensuring that every new deployment triggers an automatic reload notification in connected browsers.

## Architecture

```
┌─────────────────┐
│   Git Commit    │
│   (SHA: abc123) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      Build Arg          ┌──────────────────┐
│   build.sh /    │─────SPOT_VERSION────────▶│  Dockerfile      │
│   deploy.sh     │      = abc123            │  (bakes version) │
└─────────────────┘                          └────────┬─────────┘
                                                      │
                                                      ▼
                                             ┌──────────────────┐
                                             │  Docker Image    │
                                             │  ENV SPOT_VERSION│
                                             └────────┬─────────┘
                                                      │
         ┌────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI App (/events/version endpoint)                     │
│  - Broadcasts version via SSE every 30s                      │
│  - Sends version immediately on new connection               │
└────────┬────────────────────────────────────────────────────┘
         │
         │ Server-Sent Events (SSE)
         │ event: version_update
         │ data: {"version": "abc123"}
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Browser JavaScript (main.js)                                │
│  - Listens to SSE stream                                     │
│  - Compares received version with data-app-version attribute │
│  - Shows reload toast if different                           │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Build-Time Version Capture

**Files:** `Dockerfile`, `build.sh`, `deploy.sh`

The version is captured during Docker build:

```dockerfile
ARG SPOT_VERSION=unknown
ENV SPOT_VERSION=${SPOT_VERSION}
```

The build scripts automatically get the git SHA:

```bash
GIT_SHA=$(git rev-parse --short HEAD)
docker compose build --build-arg SPOT_VERSION="${GIT_SHA}"
```

### 2. Server-Side SSE Broadcast

**File:** `spot/main.py` (lines 244-292)

The `/events/version` endpoint:
- Creates a persistent SSE connection for each browser
- Sends initial version immediately
- Sends periodic version updates every 30 seconds
- Uses proper SSE headers (no-cache, no-buffer, keep-alive)

```python
@app.get("/events/version")
async def version_events() -> StreamingResponse:
    async def eventgen():
        ver = os.environ.get("SPOT_VERSION", "dev")
        yield f'event: version_update\ndata: {{"version": "{ver}"}}\n\n'
        # ... periodic updates every 30s
    
    return StreamingResponse(
        eventgen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

### 3. Client-Side Version Detection

**File:** `static/js/main.js` (lines 693-753)

JavaScript establishes SSE connection and compares versions:

```javascript
sseEventSource = new EventSource('/events/version');

const versionUpdateHandler = function (event) {
    const data = JSON.parse(event.data);
    const currentVersion = d.body.getAttribute('data-app-version');
    if (data.version && data.version !== currentVersion) {
        showUpdateToast();  // Shows reload notification
    }
};
```

### 4. User Notification

**File:** `templates/index.html` (lines 102-106)

Toast notification with:
- "New version available" message
- "Reload now" button
- 30-second countdown timer
- Auto-reload after countdown

## Usage

### Quick Deploy

```bash
./deploy.sh
```

This single command:
1. Gets current git SHA
2. Builds Docker image with that SHA as version
3. Restarts the application
4. Connected browsers automatically get notified

### Build Without Deploy

```bash
./build.sh          # Build only
docker compose up -d  # Deploy separately
```

### Manual Version Override

```bash
SPOT_VERSION=v1.2.3 docker compose up -d --build
```

### Check Current Version

```bash
# API endpoint
curl http://localhost:8000/version

# Docker environment
docker compose exec app env | grep SPOT_VERSION

# Browser console (when page is open)
document.body.getAttribute('data-app-version')
```

## Testing Automatic Reload

### Test 1: Initial Connection

```bash
# Terminal 1: Start app
./deploy.sh

# Terminal 2: Monitor SSE
python test_sse.py

# Expected output:
# ✓ All required SSE headers are present
# event: version_update
# data: {"type": "version", "version": "abc123"}
```

### Test 2: Version Change Detection

```bash
# Terminal 1: Deploy version 1
./deploy.sh

# Browser: Open http://localhost:8000
# Console should show: "Version update received: abc123 (current: abc123)"

# Terminal 1: Make a change and commit
git commit -m "test change" --allow-empty
./deploy.sh

# Browser: Should show reload toast
# Console: "New version available: def456 - showing reload toast"
```

### Test 3: Multiple Browsers

```bash
./deploy.sh

# Open 3 browser tabs to http://localhost:8000
# Check server logs:
docker compose logs | grep "SSE connection"

# Should see:
# New SSE connection established, total clients: 1
# New SSE connection established, total clients: 2
# New SSE connection established, total clients: 3

# Deploy new version:
./deploy.sh

# All 3 browsers should show reload notification
```

## Deployment Scenarios

### Scenario 1: Normal Git Workflow

```bash
# Make changes
git add .
git commit -m "Add new feature"

# Deploy automatically uses new commit SHA
./deploy.sh
# Version: abc123 -> def456 ✓ Triggers reload
```

### Scenario 2: Uncommitted Changes

```bash
# Make changes but don't commit
./deploy.sh

# Warning: Working directory has uncommitted changes
# Version: abc123-dirty ✓ Still triggers reload
```

### Scenario 3: No Git Repository

```bash
# In directory without .git
./deploy.sh

# Warning: Not in a git repository, using timestamp
# Version: 20231124-153045 ✓ Uses timestamp instead
```

### Scenario 4: CI/CD Pipeline

```yaml
# .github/workflows/deploy.yml example
- name: Build and deploy
  env:
    SPOT_VERSION: ${{ github.sha }}
  run: |
    docker compose build
    docker compose up -d
```

## Troubleshooting

### Reload notification not appearing

1. **Check SSE connection:**
   ```bash
   python test_sse.py
   ```
   Should show: ✓ All required SSE headers are present

2. **Check browser console:**
   Should see: "SSE connection opened successfully"
   
3. **Check server logs:**
   ```bash
   docker compose logs | grep "SSE"
   ```
   Should see: "New SSE connection established"

4. **Verify version is different:**
   ```bash
   # Old version (in browser)
   document.body.getAttribute('data-app-version')
   
   # New version (in server)
   curl http://localhost:8000/version
   ```

### SSE connection errors

1. **Behind nginx?** Check proxy configuration:
   - `proxy_buffering off;`
   - `proxy_cache off;`
   - `X-Accel-Buffering: no` header passed through

2. **Firewall?** Ensure port 8000 allows persistent connections

3. **Browser dev tools:** Network tab → filter "event-stream"
   - Should show persistent connection
   - Status: 200
   - Type: eventsource

### Version not changing

1. **Using same git commit?** Check:
   ```bash
   git rev-parse --short HEAD
   ```

2. **Not using deploy.sh?** Manual docker-compose needs version:
   ```bash
   SPOT_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
   ```

3. **Check image was rebuilt:**
   ```bash
   docker compose build --no-cache
   ```

## Benefits

1. **Zero-configuration** - Works automatically with git
2. **Deterministic** - Same commit = same version
3. **User-friendly** - Clear reload notification
4. **Production-ready** - Works behind proxies
5. **Observable** - Extensive logging for debugging

## Security Considerations

The git SHA is exposed to clients via:
- Page HTML (`data-app-version` attribute)
- `/version` API endpoint
- SSE broadcast

This is generally safe as:
- Commit SHAs are not sensitive information
- They're often visible in public git repositories
- They help with debugging and support

If you need to hide version information, consider:
- Using hash of git SHA: `echo $GIT_SHA | sha256sum | cut -c1-7`
- Using incremental numbers: `v$(date +%Y%m%d).$(git rev-list --count HEAD)`
- Restricting `/version` endpoint access in production


# Fix: Automatic Reloading Mechanism

## Problem
The automatic reloading mechanism was broken. The server-side event (SSE) connection that notifies clients about new versions wasn't working properly.

## Root Cause
The SSE endpoint (`/events/version`) was missing critical HTTP headers required for proper Server-Sent Events functionality. Without these headers:
- Proxies may cache or buffer the SSE response
- The connection may not stay alive properly
- Events may not be delivered to clients

## Solution

### 1. Added Required SSE Headers (spot/main.py)
Added the following headers to the `/events/version` endpoint's `StreamingResponse`:
- `Cache-Control: no-cache` - Prevents proxies from caching SSE responses
- `X-Accel-Buffering: no` - Disables buffering in nginx (critical for production)
- `Connection: keep-alive` - Keeps the connection alive for streaming

### 2. Enhanced Logging
Added server-side and client-side logging to help diagnose SSE issues:

**Server-side (spot/main.py):**
- Log when new SSE connections are established
- Log when initial version is sent to clients
- Log periodic version updates (debug level)
- Log when SSE connections close

**Client-side (static/js/main.js):**
- Log when SSE connection is being established
- Log when SSE connection opens successfully
- Log all version updates received with comparison to current version
- Enhanced error logging with connection state

## Testing

### Manual Testing
1. Start the application with a specific version:
   ```bash
   export SPOT_VERSION=v1.0.0
   docker compose up -d --build
   ```

2. Open the browser console and navigate to the app
   - You should see: "Establishing SSE connection to /events/version"
   - Then: "SSE connection opened successfully"
   - And: "Version update received: v1.0.0 (current: v1.0.0)"

3. Update and restart with a new version:
   ```bash
   export SPOT_VERSION=v1.0.1
   docker compose up -d --build
   ```

4. The browser should:
   - Detect the SSE connection loss
   - Automatically reconnect
   - Receive the new version
   - Log: "Version update received: v1.0.1 (current: v1.0.0)"
   - Log: "New version available: v1.0.1 - showing reload toast"
   - Display a toast notification with reload button and 30-second countdown

### Check Server Logs
Look for these log messages:
```
INFO: New SSE connection established, total clients: 1
INFO: Sending initial version to client: v1.0.1
DEBUG: Sending periodic version update to client: v1.0.1
INFO: SSE connection closed, remaining clients: 0
```

### Behind Nginx Proxy
If running behind nginx, ensure your configuration passes through SSE properly:
```nginx
location / {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
}
```

## How It Works

1. **Page Load:**
   - Browser loads page with `data-app-version` set to current version
   - JavaScript establishes SSE connection to `/events/version`
   - Server sends initial version immediately

2. **Periodic Updates:**
   - Every 30 seconds (or when no cache events occur), server sends version update
   - Client compares received version with page version
   - If different, shows reload toast

3. **Deployment:**
   - When new version is deployed, server restarts
   - All SSE connections are dropped
   - Browsers automatically reconnect (EventSource feature)
   - Server sends new version on reconnection
   - Clients detect version mismatch and show reload toast

## Important Notes

1. **Version Must Change:** For automatic reload to work, `SPOT_VERSION` environment variable must be different between deployments. Don't use the same version (like "dev") for every deployment.

2. **Set in Environment:** Make sure to set `SPOT_VERSION` in your `.env` file or deployment script:
   ```bash
   export SPOT_VERSION=$(date +%Y%m%d-%H%M%S)  # timestamp-based
   # or
   export SPOT_VERSION=$(git rev-parse --short HEAD)  # git commit hash
   ```

3. **Proxy Configuration:** If using a reverse proxy, ensure it's configured to pass through `text/event-stream` without buffering.

## Automatic Version Setting (Enhancement)

To ensure the reload mechanism triggers on every deployment, the system now automatically uses git SHA as the version.

### Changes

1. **Dockerfile** - Added build arg to capture version at build time
2. **docker-compose.yml** - Pass version as build arg
3. **build.sh** - Script to build with automatic git SHA versioning
4. **deploy.sh** - One-command deploy with automatic versioning

### Usage

Simply run:
```bash
./deploy.sh
```

This automatically:
- Captures current git commit SHA
- Builds Docker image with that version baked in
- Restarts the application
- Triggers reload notification in all connected browsers

### How It Works

1. Build script gets git SHA: `git rev-parse --short HEAD`
2. Passes to Docker as build arg: `--build-arg SPOT_VERSION=abc123`
3. Dockerfile sets as ENV variable: `ENV SPOT_VERSION=${SPOT_VERSION}`
4. Server reads from environment: `os.environ.get("SPOT_VERSION")`
5. Browser compares versions, shows reload toast if different

## Files Created
- `FIXES.md` - Detailed documentation of the fix
- `test_sse.py` - Test script to verify SSE functionality
- `build.sh` - Build script with automatic git SHA versioning
- `deploy.sh` - One-command deployment script
- `AUTOMATIC_VERSIONING.md` - Complete versioning system documentation

## Files Modified
- `Dockerfile` - Added SPOT_VERSION build arg and ENV
- `docker-compose.yml` - Pass version as build arg
- `spot/main.py` - Added SSE headers and logging
- `static/js/main.js` - Added console logging for debugging
- `README.md` - Updated documentation with automatic versioning


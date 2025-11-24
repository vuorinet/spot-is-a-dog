# Quick Start Guide - Automatic Reload Feature

## Setup (One-time)

1. Create `.env` file:
```bash
cat > .env << EOF
ENTSOE_API_TOKEN=your_token_here
DEFAULT_MARGIN_CENTS_PER_KWH=0
LOG_LEVEL=INFO
EOF
```

## Deploy New Version

**Simple method (recommended):**
```bash
./deploy.sh
```

That's it! This single command:
- ✅ Gets current git commit SHA
- ✅ Builds Docker image with version baked in
- ✅ Restarts the application
- ✅ All connected browsers get notified and show reload prompt

## How to Verify It Works

### 1. Check version was set correctly:
```bash
curl http://localhost:8000/version
# Output: {"version":"abc123"}
```

### 2. Test SSE connection:
```bash
python test_sse.py
# Should show: ✓ All required SSE headers are present
```

### 3. Open browser console:
```
http://localhost:8000
```
Console should show:
```
Establishing SSE connection to /events/version
SSE connection opened successfully  
Version update received: abc123 (current: abc123)
```

### 4. Deploy new version:
```bash
git commit -m "test" --allow-empty
./deploy.sh
```

Browser should automatically show:
```
┌─────────────────────────────────────┐
│  New version available              │
│  [Reload now]  Auto-reload in 30s   │
└─────────────────────────────────────┘
```

## Troubleshooting

### "Version not changing"
Make sure you committed your changes:
```bash
git status
git add .
git commit -m "your changes"
./deploy.sh
```

### "SSE connection error"
Check server logs:
```bash
docker-compose logs -f | grep SSE
```

Should see:
```
New SSE connection established, total clients: 1
Sending initial version to client: abc123
```

### "No reload notification"
1. Check versions are different:
```bash
# In browser console:
document.body.getAttribute('data-app-version')

# On server:
curl http://localhost:8000/version
```

2. Check browser console for SSE messages

3. Run test script:
```bash
python test_sse.py
```

## Alternative Methods

### Build only (no deploy):
```bash
./build.sh
docker-compose up -d
```

### Manual version:
```bash
SPOT_VERSION=v1.2.3 docker-compose up -d --build
```

### Quick restart (no rebuild):
```bash
docker-compose restart
```

## What Gets Versioned?

- ✅ Git commit SHA (short form, e.g., `abc123`)
- ✅ Includes `-dirty` suffix if uncommitted changes exist
- ✅ Falls back to timestamp if not in git repo
- ✅ Version is baked into Docker image (survives restarts)

## Production Deployment

For production with nginx, add to your nginx config:

```nginx
location / {
    proxy_pass http://localhost:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
}
```

This ensures SSE streams work properly through the proxy.

## Summary

**Before:** Manual refresh needed, users might run stale version

**After:** 
1. Developer: `./deploy.sh`
2. Browser: *Shows reload notification automatically*
3. User: *Clicks "Reload now" or waits 30s*
4. Done! ✅

---

For more details, see:
- `AUTOMATIC_VERSIONING.md` - Complete technical documentation
- `FIXES.md` - What was fixed and why
- `test_sse.py` - SSE connection testing tool


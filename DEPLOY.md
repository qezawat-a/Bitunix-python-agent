# Deployment Guide — GitHub Actions + VPS

## Options

### 1. **GitHub Actions (Not Recommended for 24/7)**
- ❌ Max 6 hours per job
- ❌ Better for scheduled tasks
- ✅ Great for CI/CD testing
- ✅ Great for auto-deploy to VPS

### 2. **VPS/Server (Recommended for 24/7)** ⭐
- ✅ Runs 24/7
- ✅ Full control
- ✅ Auto-restart on failure
- ✅ GitHub Actions can auto-deploy to it

### 3. **Docker + Cloud** (Advanced)
- ✅ Containerized
- ✅ Easy scaling
- ⚠️ More complex setup

---

## Recommended Setup: VPS + GitHub Actions

### Step 1: Get a VPS

Options:
- **Vultr** ($2.50/month) — Cheapest
- **DigitalOcean** ($4-6/month) — Reliable
- **Linode** ($5/month) — Good performance
- **Hetzner** ($4/month) — Best value

Requirements:
- Ubuntu 20.04 or later
- 1GB RAM minimum
- 10GB storage

### Step 2: Initial VPS Setup

```bash
# SSH into VPS
ssh root@YOUR_VPS_IP

# Create user for bot
useradd -m -s /bin/bash j-rock
usermod -aG sudo j-rock

# Switch to bot user
su - j-rock

# Clone repo
git clone https://github.com/qezawat-a/Bitunix-python-agent.git
cd Bitunix-python-agent

# Setup Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env
nano .env
# Paste your config, save (Ctrl+X, Y, Enter)

# Test the bot
python3 run.py
# Should show: [jrock] ready as ...
# Press Ctrl+C to stop
```

### Step 3: Create Systemd Service

```bash
# As root (exit from j-rock user first)
exit

# Copy service file
cp /home/j-rock/Bitunix-python-agent/j-rock-bot.service /etc/systemd/system/

# Enable and start
systemctl daemon-reload
systemctl enable j-rock-bot
systemctl start j-rock-bot

# Check status
systemctl status j-rock-bot

# View logs
journalctl -u j-rock-bot -f
```

### Step 4: Setup GitHub Secrets

Go to GitHub repo → Settings → Secrets and variables → Actions

Add these secrets:
```
VPS_HOST              = your.vps.ip.address
VPS_USER              = j-rock
VPS_PORT              = 22
VPS_PRIVATE_KEY       = (your SSH private key)
```

#### Generate SSH Key for Deployment

```bash
# On your local machine
ssh-keygen -t rsa -b 4096 -f deploy_key -N ""

# Copy public key to VPS
ssh-copy-id -i deploy_key.pub j-rock@YOUR_VPS_IP

# Add private key to GitHub:
# Copy contents of deploy_key (without .pub)
# Paste into GitHub secret VPS_PRIVATE_KEY
```

### Step 5: GitHub Actions Will Auto-Deploy

Now whenever you push to `main`:

1. **Test workflow** runs (checks syntax)
2. **Deploy workflow** runs (SSH to VPS, pull changes, restart bot)

```bash
git add .
git commit -m "updates"
git push origin main

# Watch GitHub Actions tab for deployment
```

---

## Workflow Files Included

### `.github/workflows/test.yml`
- Runs on every push
- Checks Python syntax
- Validates all modules
- No secrets needed

### `.github/workflows/deploy.yml`
- Runs after test succeeds
- SSHs to your VPS
- Pulls latest code
- Restarts bot service

---

## Manual Deployment (No GitHub Actions)

If you prefer manual control:

```bash
# On VPS, pull latest code
cd ~/Bitunix-python-agent
git pull origin main
source venv/bin/activate
pip install -r requirements.txt

# Restart service
sudo systemctl restart j-rock-bot

# Check status
journalctl -u j-rock-bot -f
```

---

## Monitoring

### Check Bot Status

```bash
# SSH to VPS
ssh j-rock@YOUR_VPS_IP

# View logs
journalctl -u j-rock-bot -f

# Check if running
systemctl status j-rock-bot

# Restart if needed
sudo systemctl restart j-rock-bot
```

### From Telegram

The bot sends you notifications:
- ✅ Position opened
- ✅ Breakeven set
- 📈 Trailing activated
- 🔒 Position closed
- ❌ Errors (if any)

---

## Troubleshooting

### Bot Not Starting

```bash
# Check logs
journalctl -u j-rock-bot -n 50

# Common issues:
# - .env file missing → cp .env.example .env
# - Permission denied → chown j-rock:j-rock .env
# - Python not found → check venv path in service file
```

### GitHub Actions Deploy Fails

```bash
# Check:
1. VPS_HOST is correct (not hostname, must be IP)
2. VPS_PRIVATE_KEY is correct (full private key, no .pub)
3. j-rock user exists on VPS
4. SSH key authorized on VPS (ssh-copy-id)
5. Port 22 is open on VPS firewall
```

### Bot Stops Running

The systemd service auto-restarts after 10 seconds:

```ini
Restart=always
RestartSec=10
```

But check logs for why it stopped:

```bash
journalctl -u j-rock-bot -n 100 | grep -i error
```

---

## Update Bot

### Option 1: Push to GitHub (Auto-Deploy)

```bash
# Make changes locally
git add .
git commit -m "your changes"
git push origin main

# Automatically:
# 1. Tests run
# 2. Deploy workflow runs
# 3. VPS pulls changes
# 4. Bot restarts
```

### Option 2: Manual SSH Update

```bash
ssh j-rock@YOUR_VPS_IP
cd ~/Bitunix-python-agent
git pull origin main
pip install -r requirements.txt  # if deps changed
sudo systemctl restart j-rock-bot
```

---

## Backup & Restore

### Backup Database

```bash
# SSH to VPS
scp j-rock@YOUR_VPS_IP:~/Bitunix-python-agent/data/agent.db ~/backup.db

# Or regularly:
# Add to crontab: 0 2 * * * scp j-rock@VPS:~/Bitunix-python-agent/data/agent.db ~/backups/agent-$(date +\%Y\%m\%d).db
```

### Backup .env

```bash
# IMPORTANT: Keep .env secure
scp j-rock@YOUR_VPS_IP:~/Bitunix-python-agent/.env ~/backup.env.secure
```

---

## Cost

| Service | Cost | Specs |
|---------|------|-------|
| Vultr | $2.50/mo | 512MB RAM, 10GB SSD |
| DigitalOcean | $4/mo | 512MB RAM, 10GB SSD |
| Linode | $5/mo | 1GB RAM, 25GB SSD |
| Hetzner | €4/mo | 2GB RAM, 40GB SSD |

**Total:** ~$4-5/month for 24/7 bot

---

## Advanced: Multiple Instances

Run bot on multiple VPS for redundancy:

```bash
# VPS1: Main bot
# VPS2: Standby bot (in case VPS1 fails)

# GitHub Actions can deploy to both:
.github/workflows/deploy-vps1.yml
.github/workflows/deploy-vps2.yml
```

---

## Monitoring & Alerts

### Setup Email Alerts (Optional)

Install `ssmtp` for error notifications:

```bash
sudo apt-get install ssmtp

# Configure to send emails if bot crashes
```

### Setup Uptime Monitoring

Services like:
- UptimeRobot (free)
- Pingdom
- Uptime.com

Just ping a health endpoint or check if bot is responsive.

---

## Summary

✅ **Recommended Setup:**
1. Get $5/month VPS
2. Install bot + systemd service
3. Add GitHub Secrets
4. Push to main → Auto-deploy ✨

✅ **2 Workflows Included:**
- `test.yml` — Validates code
- `deploy.yml` — Auto-deploys to VPS

✅ **Bot runs 24/7** with auto-restart

✅ **One-command updates:** `git push origin main`

---

## Next: Check Bot is Running

```bash
# SSH to VPS
ssh j-rock@YOUR_VPS_IP
journalctl -u j-rock-bot -f

# Should see:
# [jrock] ready as YOUR_BOT_NAME
# [jrock] starting polling…
```

Done! 🚀

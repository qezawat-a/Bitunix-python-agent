# GitHub Actions — First Time Guide 🚀

## What is GitHub Actions?

GitHub Actions lets you **run code on GitHub's servers** without needing your own computer to be on.

**Simple analogy:** It's like hiring someone to run your bot for you whenever you ask.

---

## How to Use (Super Simple)

### Step 1: Add GitHub Secrets

**Why?** Store your API keys securely (don't commit them to GitHub)

**How:**
1. Go to your repo on GitHub
2. Click **Settings** (top right)
3. Click **Secrets and variables** → **Actions** (left sidebar)
4. Click **New repository secret** (green button)

**Add these secrets:**

```
Name: TELEGRAM_BOT_TOKEN
Value: 8800573659:AAEKo7YOJB48eJrcq1f-msm6awBrYJZ_vqs

Name: TELEGRAM_ADMIN_IDS
Value: 8368047336

Name: AI_BASE_URL
Value: http://127.0.0.1:20128/v1

Name: AI_API_KEY
Value: sk-fd1a6442096d6cdf-jkt266-1cec6ecd

Name: BITUNIX_API_KEY
Value: 8f595df10b52d3bd1a324f6c64aa4c34

Name: BITUNIX_SECRET_KEY
Value: 59d0c38837b56faa7cb9c945c691cbe9
```

**That's it!** GitHub will use these instead of your `.env` file.

---

### Step 2: Run the Bot on GitHub Actions

1. Go to your repo
2. Click **Actions** (top menu)
3. Click **Run Bot (Manual)** (left sidebar)
4. Click **Run workflow** (blue button)
5. Click **Run workflow** again (in popup)

**Done!** Bot is running on GitHub servers now.

---

### Step 3: Check Logs

1. Click on the running workflow
2. Click **run-bot**
3. Scroll down to see **output**
4. Should see:
   ```
   [jrock] ready as YOUR_BOT_NAME
   [jrock] starting polling…
   ```

---

## That's All!

Now your Telegram bot is **live and running** on GitHub!

You can:
- Send it messages: `/chat "hello"`
- Start trading: `/trader start BTCUSDT 5m`
- Check positions: `/trader positions`

**While it's running**, GitHub keeps it alive for up to **6 hours**.

---

## What's Happening Behind the Scenes?

```
1. You click "Run workflow" on GitHub
2. GitHub spins up a Linux computer
3. Installs Python + your code
4. Creates .env from your secrets
5. Runs: python3 run.py
6. Bot connects to Telegram
7. You can use it like normal
8. After 6 hours, it stops (or when you stop it)
```

---

## How to Stop It

1. Go to **Actions**
2. Click the running workflow
3. Click **Cancel workflow** (red button)

**Done!** Bot stops immediately.

---

## Tips

### Run It on a Schedule

Edit `.github/workflows/run-bot.yml`:

```yaml
on:
  schedule:
    - cron: '0 9 * * MON-FRI'  # Every weekday at 9 AM UTC
  workflow_dispatch:  # Also allow manual trigger
```

Then bot runs automatically every weekday!

### Run It Longer

Default is 6 hours. To change:

In `.github/workflows/run-bot.yml`, change:
```yaml
timeout-minutes: 360  # 360 = 6 hours
```

To 2 hours:
```yaml
timeout-minutes: 120  # 2 hours
```

### View All Runs

**Actions** tab shows history of all bot runs:
- ✅ Successful
- ❌ Failed
- ⏱️ Running now

Click any to see full logs.

---

## Limitations (Important!)

- ⏱️ **Max 6 hours per run** (GitHub limit)
- 🌍 **Not 24/7** (stops after 6 hours)
- 🆓 **Free tier has 2000 minutes/month**

**For production 24/7:** Use VPS instead (see DEPLOY.md)

**For testing/learning:** This is perfect! ✅

---

## Troubleshooting

### Bot Doesn't Start

Check logs in Actions tab:
1. Go **Actions** → **Run Bot (Manual)**
2. Click latest run
3. Click **run-bot**
4. Scroll down to **Run Bot** step
5. Look for error messages

Common errors:
- ❌ `ModuleNotFoundError` → Check requirements.txt
- ❌ `TELEGRAM_BOT_TOKEN: None` → Secret not set correctly
- ❌ `Connection refused` → API endpoint not reachable

### Bot Stops Early

- ⏱️ Ran out of time (6 hour limit)
- ❌ Crashed (check logs)
- 🔌 Lost connection (network issue)

### I Don't Get Messages in Telegram

- Check bot token is correct
- Check admin ID is correct (your Telegram user ID)
- Check bot hasn't hit rate limits
- Restart the workflow

---

## Cost

**FREE!** ✅

GitHub gives you:
- 2000 free minutes/month
- That's ~32 hours/month
- Or 6-7 runs of 5 hours each

---

## Next Steps

1. ✅ Add GitHub Secrets (Step 1 above)
2. ✅ Run the workflow (Step 2 above)
3. ✅ Test in Telegram: `/chat "hello"`
4. ✅ Try trading: `/trader start BTCUSDT 5m`
5. ✅ Watch the logs

**Enjoy!** 🎉

---

## Want 24/7 Bot?

See `DEPLOY.md` — Run on a VPS for ~$5/month.

**But GitHub Actions is great for:**
- Learning & testing
- Running bot for specific hours
- Weekly trading sessions
- Development & debugging

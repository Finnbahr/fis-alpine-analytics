# 🚀 Your Deployment Guide - Start Here!

**Status**: ✅ Git repository ready, code committed
**Next**: Push to GitHub → Deploy backend → Deploy frontend

---

## Step 1: Push to GitHub (5 minutes)

### Option A: Using GitHub CLI (Automatic) ⭐

```bash
# Login to GitHub
gh auth login
# Follow prompts: GitHub.com → HTTPS → Login with browser

# Create repository and push
cd "/Users/finnbahr/Desktop/FIS Scraping"
gh repo create fis-alpine-analytics --public --source=. --push

# Done! Your code is on GitHub
```

### Option B: Manual (via GitHub Website)

1. Go to **https://github.com/new**
2. **Repository name**: `fis-alpine-analytics`
3. **Description**: `Full-stack FIS Alpine skiing analytics with FastAPI + React`
4. Choose **Public** or **Private**
5. **Don't** initialize with README (we have one)
6. Click **Create repository**

Then run these commands:
```bash
cd "/Users/finnbahr/Desktop/FIS Scraping"
git remote add origin https://github.com/YOUR-USERNAME/fis-alpine-analytics.git
git branch -M main
git push -u origin main
```

**✅ Checkpoint**: Visit your GitHub repo URL to verify code is there

---

## Step 2: Deploy Backend to Render (15 minutes)

### 2.1 Create Render Account

1. Go to **https://render.com**
2. Click **Get Started for Free**
3. Sign up with **GitHub** (easiest - auto-connects repos)
4. Authorize Render

### 2.2 Create PostgreSQL Database FIRST

**Important**: You need a cloud database before deploying the API!

1. In Render Dashboard → **New +** → **PostgreSQL**
2. Fill in:
   - **Name**: `fis-alpine-db`
   - **Database**: `alpine_analytics`
   - **User**: `alpine_analytics`
   - **Region**: **Oregon** (or closest to you)
   - **Plan**: **Free** (90 days) or **Starter $7/month**
3. Click **Create Database**
4. Wait 2-3 minutes for creation

5. **SAVE THESE** (click "Info" tab):
   - Internal Database URL
   - External Database URL
   - Hostname
   - Port
   - Database name
   - Username
   - Password

### 2.3 Migrate Your Data to Cloud

```bash
# Export from local database
pg_dump -h 127.0.0.1 -p 5433 -U alpine_analytics -d alpine_analytics -F c -f ~/Desktop/fis_backup.dump

# Import to Render (use values from Render DB Info)
pg_restore -h YOUR-RENDER-HOST -p 5432 -U alpine_analytics -d alpine_analytics -v ~/Desktop/fis_backup.dump

# If you get "already exists" errors, that's OK - it means data is migrating
```

**Alternative**: Use connection string from Render:
```bash
pg_restore -d "postgres://USER:PASSWORD@HOST/alpine_analytics" ~/Desktop/fis_backup.dump
```

**This will take 10-30 minutes depending on your internet speed!**

### 2.4 Deploy FastAPI Application

1. Render Dashboard → **New +** → **Web Service**
2. Click **Connect account** if needed
3. Find **fis-alpine-analytics** → **Connect**

4. Configure:
   - **Name**: `fis-alpine-api`
   - **Region**: **Same as database** (Oregon)
   - **Branch**: `main`
   - **Root Directory**: `fis-api`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: **Free** (or **Starter $7/month**)

5. **Environment Variables** - Add each:

   | Variable | Value |
   |----------|-------|
   | `DB_HOST` | (from Render DB - internal hostname) |
   | `DB_PORT` | `5432` |
   | `DB_USER` | `alpine_analytics` |
   | `DB_PASSWORD` | (from Render DB) |
   | `DB_NAME` | `alpine_analytics` |
   | `RAW_DB_NAME` | `alpine_analytics` |
   | `AGGREGATE_DB_NAME` | `alpine_analytics` |
   | `CORS_ORIGINS` | `*` (temp - we'll fix later) |

6. Click **Create Web Service**

7. **Wait 5-10 minutes** - watch the logs

8. When you see "Application startup complete", **copy your API URL**:
   ```
   https://fis-alpine-api.onrender.com
   ```

9. **Test it**:
   - Health: `https://fis-alpine-api.onrender.com/health`
   - Docs: `https://fis-alpine-api.onrender.com/docs`
   - Endpoint: `https://fis-alpine-api.onrender.com/api/v1/leaderboards/Slalom?limit=3`

**✅ Checkpoint**: All 3 URLs should work!

---

## Step 3: Deploy Frontend to Vercel (5 minutes)

### 3.1 Create Vercel Account

1. Go to **https://vercel.com**
2. Click **Sign Up**
3. Choose **Continue with GitHub**
4. Authorize Vercel

### 3.2 Deploy

1. Click **Add New...** → **Project**
2. Import **fis-alpine-analytics**
3. Click **Import**

4. Configure:
   - **Framework Preset**: Vite (auto-detected)
   - **Root Directory**: `fis-frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
   - **Install Command**: `npm install`

5. **Environment Variables**:
   - **Key**: `VITE_API_BASE_URL`
   - **Value**: `https://fis-alpine-api.onrender.com/api/v1`
     *(use YOUR Render URL from Step 2)*

6. Click **Deploy**

7. Wait 1-2 minutes

8. **Your app is live!** 🎉
   ```
   https://fis-alpine-analytics.vercel.app
   ```

**✅ Checkpoint**: Open the URL - app should load!

---

## Step 4: Update CORS (2 minutes)

Now update backend to allow only your frontend:

1. Render Dashboard → **fis-alpine-api**
2. **Environment** tab
3. Find `CORS_ORIGINS`
4. Update to: `https://YOUR-APP.vercel.app`
   *(use YOUR actual Vercel URL)*
5. Click **Save Changes**
6. Service auto-redeploys (2-3 min)

**✅ Checkpoint**: Refresh your Vercel app - everything should work!

---

## Step 5: Verify Everything Works (5 minutes)

### Backend Tests
- [ ] Health: `https://YOUR-API.onrender.com/health`
- [ ] API Docs: `https://YOUR-API.onrender.com/docs`
- [ ] Leaderboard: `https://YOUR-API.onrender.com/api/v1/leaderboards/Slalom?limit=5`
- [ ] Hot Streak: `https://YOUR-API.onrender.com/api/v1/leaderboards/hot-streak?limit=5`

### Frontend Tests
- [ ] Home page loads with data
- [ ] Click "Leaderboards" → see rankings
- [ ] Click an athlete → profile loads
- [ ] Press Cmd+K → search works
- [ ] Go to Courses → data loads
- [ ] Go to Analytics → charts appear
- [ ] Browser console (F12) → no errors

### Mobile Test
- [ ] Open on phone or resize browser
- [ ] Navigation works
- [ ] Charts responsive
- [ ] Tables scrollable

---

## 🎉 Success! Your App is Live!

**URLs**:
- **Frontend**: https://YOUR-APP.vercel.app
- **Backend**: https://YOUR-API.onrender.com
- **API Docs**: https://YOUR-API.onrender.com/docs

**GitHub**: https://github.com/YOUR-USERNAME/fis-alpine-analytics

---

## 📊 What You Deployed

✅ **FastAPI Backend**
- 15 REST endpoints
- PostgreSQL database (1.5M results)
- Automatic health checks
- API documentation

✅ **React Frontend**
- 5 interactive pages
- Real-time data from API
- Mobile responsive
- Global search

✅ **Production Features**
- Error handling
- Loading states
- CORS configured
- Environment variables
- Automatic deployments (on git push!)

---

## 🔧 Updating Your App

### Push Code Changes
```bash
cd "/Users/finnbahr/Desktop/FIS Scraping"
git add .
git commit -m "Your change description"
git push
```

**Both Render and Vercel auto-deploy!** ✨

### Update Database
```bash
cd "/Users/finnbahr/Desktop/FIS Scraping/alpine analytic database"
source ../fis-api/venv/bin/activate
python3 run_daily_update.py
```

---

## 💰 Costs

### Current (Free Tier)
- Render Backend: **Free** (spins down after 15 min idle)
- Render Database: **Free for 90 days** then $7/month
- Vercel Frontend: **Free** (100 GB bandwidth)

**Total**: $0/month for 90 days

### Production (Always-On)
- Render Backend: **$7/month** (Starter)
- Render Database: **$7/month**
- Vercel Pro: **$20/month** (optional)

**Total**: $14-34/month

---

## 🐛 Troubleshooting

### "Failed to fetch" in frontend
- Check browser console for error
- Verify `VITE_API_BASE_URL` is correct in Vercel
- Verify backend URL in CORS_ORIGINS

### Backend "Database connection failed"
- Check all DB environment variables
- Test database connection in Render logs
- Verify database is running

### Slow first request
- Free tier spins down after 15 min
- First request takes ~30 sec to wake up
- Upgrade to Starter ($7/month) for always-on

---

## 🎯 Next Steps

**Share your app**:
- Add to resume/portfolio
- Share on LinkedIn
- Post on Reddit r/skiing
- Add custom domain (optional)

**Monitor**:
- Set up UptimeRobot (free monitoring)
- Check Render logs regularly
- Review Vercel analytics

**Iterate**:
- Add features from user feedback
- Update with 2026 season data
- Expand analytics

---

**Last Updated**: February 14, 2026
**Status**: ✅ Ready to Deploy!

**Questions?** Check logs in Render/Vercel dashboards

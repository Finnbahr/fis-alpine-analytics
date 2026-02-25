# ⛷️ Alpine Analytics Pro

Professional athlete analytics platform for FIS Alpine skiing with advanced metrics and interactive visualizations.

![Tech Stack](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=flat&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)

## 📊 Overview

High-tech analytics platform featuring:

- **Dashboard**: Browse and search through 29,000+ athlete profiles
- **Athlete Profiles**: Comprehensive analytics with 3 detailed tabs
- **Advanced Metrics**: Z-scores, strokes gained, bib advantage, regression analysis
- **Live Calculations**: Dynamic filtering by year and discipline
- **Dark Theme**: Professional data visualization interface

## 🎯 Features

### 🏠 Dashboard
- Grid of athlete cards with key stats
- Search and filter by discipline, tier, country
- Momentum indicators and recent form
- Click any athlete to view full profile

### 👤 Athlete Profile - 3 Tabs

**Races Tab**
- Complete race history with results
- Z-scores and strokes gained metrics
- Bib-relative performance analysis
- Filter by year and discipline

**Momentum Tab**
- Performance trends over time
- Momentum tracking charts
- FIS points progression

**Course Analysis Tab**
- Regression analysis: How course characteristics affect performance
- Course trait performance: Vertical drop, gate count, altitude quintiles
- Top performing courses with win rates
- All charts respond to year/discipline filters with live calculations

## 🏗️ Architecture

```
fis-alpine-analytics/
├── fis-api/              # FastAPI Backend
│   ├── app/
│   │   ├── main.py      # Application entry
│   │   ├── config.py    # Configuration
│   │   ├── database.py  # PostgreSQL connection
│   │   └── routers/     # API endpoints
│   └── requirements.txt
│
├── fis-frontend/         # React + TypeScript Frontend
│   ├── src/
│   │   ├── components/  # UI components & charts
│   │   ├── pages/       # Dashboard & AthleteProfile
│   │   ├── services/    # API client
│   │   └── types/       # TypeScript types
│   └── package.json
│
└── README.md
```

## 🚀 Local Development

### Prerequisites
- Python 3.13+
- Node.js 20+
- PostgreSQL 14+ with alpine_analytics database

### 1. Start Backend
```bash
cd fis-api
source venv/bin/activate
uvicorn app.main:app --reload
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### 2. Start Frontend
```bash
cd fis-frontend
npm install
npm run dev
# App: http://localhost:5173
```

## 🔌 Key API Endpoints

### Athletes
- `GET /api/v1/athletes` - List athletes (with filters)
- `GET /api/v1/athletes/{fis_code}` - Athlete profile
- `GET /api/v1/athletes/{fis_code}/races` - Race history
- `GET /api/v1/athletes/{fis_code}/momentum` - Momentum data
- `GET /api/v1/athletes/{fis_code}/strokes-gained` - Strokes gained metrics
- `GET /api/v1/athletes/{fis_code}/strokes-gained-bib` - Bib advantage
- `GET /api/v1/athletes/{fis_code}/regression` - Course regression (live calc when year provided)
- `GET /api/v1/athletes/{fis_code}/course-traits` - Trait quintiles (live calc when year provided)

### Search
- `GET /api/v1/search` - Global athlete search

## 🚢 Deployment

**Backend**: Deployed on Render
**Frontend**: Deployed on Vercel
**Database**: PostgreSQL on Render

### Environment Variables

**Frontend** (Vercel):
```
VITE_API_BASE_URL=https://your-api.onrender.com/api/v1
```

**Backend** (Render):
```
DB_HOST=your-db-host
DB_PORT=5432
DB_USER=alpine_analytics
DB_PASSWORD=your-password
DB_NAME=alpine_analytics
CORS_ORIGINS=https://your-app.vercel.app
```

## 🛠️ Tech Stack

**Backend**: FastAPI, PostgreSQL, Pydantic, Uvicorn
**Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Recharts
**Deployment**: Render (API + DB), Vercel (Frontend)

## 📊 Data

- **6.7M+ race results** from official FIS data
- **29,000+ athlete profiles** with career statistics
- **35,000+ races** across 1,300+ locations
- **Pre-computed aggregates** for fast loading
- **Live calculations** for dynamic filtering

## 🎨 Theme

High-tech dark theme with:
- Pure black backgrounds (`#000000`)
- Cyan accents (`#06b6d4`)
- Emerald for positive metrics (`#10b981`)
- Red for negative metrics (`#ef4444`)
- Glassmorphism effects

## 📱 Demo

1. **Dashboard**: Browse athlete cards, use search/filters
2. **Click athlete**: View comprehensive profile
3. **Races tab**: See race history with z-scores and strokes gained
4. **Course Analysis**: View regression charts and trait performance
5. **Filter**: Change year or discipline - all charts update in real-time

## 📊 Key Metrics

- **API Response**: < 100ms average
- **Live Calculations**: SQL CORR(), REGR_R2(), PERCENTILE_CONT()
- **Charts**: Recharts with 60 FPS rendering
- **Mobile**: Fully responsive design

## 🔧 Advanced Features

### Hybrid Data Strategy
- **Pre-computed aggregates**: Fast loading when no year filter
- **Live calculations**: Dynamic SQL when year filter applied
- **Client-side filtering**: Instant updates for race history
- **Unified filtering**: Year + discipline affect all tabs consistently

### Statistical Metrics
- **Z-scores**: Performance relative to field
- **Strokes Gained**: Advantage/disadvantage vs average
- **Bib Advantage**: Start position impact
- **Regression Analysis**: Course characteristic correlations
- **Trait Quintiles**: Performance by course type

## 📝 License

Educational and portfolio purposes.
Data sourced from FIS (International Ski Federation).

---

**Built with ⛷️ by a skiing analytics enthusiast**
**Status**: ✅ Production Ready

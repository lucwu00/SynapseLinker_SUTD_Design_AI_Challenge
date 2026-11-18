# Hosting: Render (backend) + Vercel (dashboard)

```
Vercel  https://synapse-linker.vercel.app       React dashboard, wall display, robot screens
   │  VITE_API_BASE
   ▼
Render  https://synapse-linker-api.onrender.com FastAPI + trained models + SQLite (Singapore region)
```

**Use the hosted version as a public showcase link (judges, report, portfolio).** For the live robot demo on 20 Nov, run the backend on the system laptop (`start_backend_robot.bat`):

| | Laptop backend | Render free plan |
|---|---|---|
| Robot command delay | ~0.1 s on the hotspot | 0.3–1 s per poll over the internet |
| Wakes up | Always on | Sleeps after 15 min idle; the first request takes about a minute |
| Demo state | Kept until you reset | Lost whenever Render restarts the service |
| Venue Wi-Fi needed | No (own hotspot) | Yes, for every device |

## 1. Put the project on GitHub (once)

Create an empty **private** repository `synapse-linker` at github.com (account lucwu00), without a README. Then in the project folder:

```
git init
git add .
git commit -m "Synapse Linker"
git branch -M main
git remote add origin https://github.com/lucwu00/synapse-linker.git
git push -u origin main
```

`.gitignore` already leaves out `.venv`, `node_modules` and the runtime database. The datasets (about 70 MB) are included because Render trains the models from them; GitHub warns about the 48 MB events file but accepts it.

## 2. Backend on Render

1. render.com → sign in with GitHub → **New → Blueprint** → choose `synapse-linker`. Render reads `render.yaml`.
2. Click **Apply**. The build installs the packages and trains Model A, Model B and the wait profile from the datasets (about 3–5 minutes, peak ~400 MB).
3. When it is live, open `https://<your-service>.onrender.com/health`. It should return `{"ok": true, ...}`. Copy this base URL.

What `render.yaml` sets:

| Variable | Why |
|---|---|
| `SYNAPSE_DEPLOY_TRAIN=1` | Trains only the selected model family (HistGradientBoosting) so the build fits in 512 MB. Same features, split and seeds as the laptop |
| `SYNAPSE_DISABLE_LIVE_REPLAY=1` | The "re-run replay" button needs more memory than the free plan has; the stored held-out results still show |
| `SYNAPSE_MAP=config/table_map_60cm.json` | The table loop on the floor map |

EDi-01 stays on the simulation adapter on Render. To drive the real robot through Render instead (not recommended for the demo), add `SYNAPSE_ROBOT_ADAPTER=esp32` and set `BACKEND_URL` in the firmware's `config.h` to the `https://…onrender.com` URL.

## 3. Dashboard on Vercel

1. vercel.com → sign in with GitHub → **Add New → Project** → import `synapse-linker`.
2. **Root Directory:** `frontend`. Framework preset: **Vite** (build `npm run build`, output `dist`).
3. **Environment Variables:** `VITE_API_BASE` = your Render URL, e.g. `https://synapse-linker-api.onrender.com` (no trailing slash).
4. **Deploy.** Open the Vercel URL:

| Screen | URL |
|---|---|
| Wall display | `https://<project>.vercel.app/#/` |
| Presenter console | `https://<project>.vercel.app/#/presenter` |
| Robot screens | `https://<project>.vercel.app/#/robot/EDi-01`, `#/robot/EDi-02` |

If the backend is asleep, the page says it is waking the hosted server and retries by itself.

Changed `VITE_API_BASE` later? Redeploy on Vercel: the value is built into the page.

## 4. Before presenting the hosted link

- Open `/health` on Render about 2 minutes before, so it is awake.
- Everyone with the link shares **one** clinic state and can press the demo buttons. Click **Reset demo** before showing it.
- The hosted site carries the same "concept prototype, not affiliated with CGH" notice and synthetic data only.

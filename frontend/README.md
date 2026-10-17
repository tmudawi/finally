# FinAlly frontend

Next.js (App Router, static export), Tailwind v4, Lightweight Charts v5.

```bash
npm install
npm run build   # static export to out/
npm test        # Vitest + React Testing Library
npm run dev     # dev server on :3000; set NEXT_PUBLIC_API_BASE=http://localhost:8000
```

The backend serves `out/` (via `STATIC_DIR`) so all API calls are same-origin in production.

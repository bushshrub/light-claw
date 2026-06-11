# Frontend

- Use **SvelteKit** for any web UI work. No vanilla HTML/JS/CSS.
- Use **pnpm** as the package manager. Not npm, not yarn.
- Frontend lives in `lightclaw-webui/`. Build output goes to `lightclaw-webui/build/`.
- Always run `pnpm build` from `lightclaw-webui/` before testing the FastAPI-served UI.
- Dev hot-reload: `pnpm dev` (Vite on :5173) proxies `/api` → FastAPI on :8000.
- Svelte 5 runes mode is active — use `$state`, `$derived`, `$props`, `$bindable`. No stores.

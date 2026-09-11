# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  # Sendex Frontend

  React and TypeScript control plane for campaigns, templates, recipient lists, sending accounts, replies, warm-up, observability, and staged AI-assisted imports.

  ## Stack

  - React 19 and TypeScript
  - Vite
  - Tailwind CSS
  - SWR and Axios
  - Vitest and Testing Library
  - ESLint

  ## Development

  ```bash
  npm ci
  cp .env.example .env.local
  npm run dev
  ```

  The development server runs on `http://localhost:3001`. Vite proxies `/api`, `/track`, `/unsubscribe`, and `/health` requests to the FastAPI service on port `8090`.

  ## Configuration

  `VITE_API_KEY` is used for local API access. Never embed privileged production credentials in a frontend build; browser-delivered values are visible to users.

  ## Quality checks

  ```bash
  npm test
  npm run lint
  npm run build
  ```

  ## Structure

  - `src/pages/` contains route-level product screens.
  - `src/components/` contains navigation, editors, dashboards, and assistant UI.
  - `src/contexts/` owns authentication and theme state.
  - `src/api.ts` defines the typed HTTP client.
  - `src/types.ts` contains shared API-facing types.
  - `src/templates/` contains reusable message layouts.

  The interface defaults to a dense, black-first operations theme designed for long-running campaign monitoring.
      parserOptions: {

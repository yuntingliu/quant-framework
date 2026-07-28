# Dashboard Vertical Slice

This example shows the two ends of one small feature:

- `router.py` owns a typed, bounded FastAPI response.
- `ExampleScoreWidget.tsx` owns loading, error, retry, and ready states.

For a real contribution, put the router under `dashboard/backend/routers`,
include it in `dashboard/backend/main.py`, put the widget under
`dashboard/frontend/src/widgets`, and register it in both registry files. Add a
FastAPI contract test and verify the widget in Dockview at desktop and narrow
viewport sizes.

Do not activate a widget until its backend contract exists. Runtime profile
requests must never silently fall back to Demo.

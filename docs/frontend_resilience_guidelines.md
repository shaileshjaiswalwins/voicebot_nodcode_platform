# Frontend Resilience Guidelines

## Patterns Implemented

1. Every API call times out after 4 seconds and returns a constructive error.
2. Dashboard refresh uses partial success. If one API fails, successful sections still render.
3. Successful API responses are cached in `localStorage` as last-known-good data.
4. Dashboard-wide diagnostics show the latest failing subsystem and fallback actions.
5. Each major view is wrapped in a local error boundary.
6. Crashed widgets show `Reset local state` and `Bypass / inspect diagnostics` actions.
7. Failed actions transform buttons into explicit fallback/retry actions.
8. WebRTC test failures show retry and skip paths without clearing entered metadata.

## Rules For New Screens

- Do not use raw `Promise.all` for dashboard-wide loading unless all results are truly required.
- Prefer `Promise.allSettled` for cross-section refreshes.
- Always keep last-known-good state visible when fresh data fails.
- Report localized failures to dashboard diagnostics.
- Every primary action should have a retry or bypass path.
- Any risky component should be inside `ResilientPanel`.
- Do not require users to open browser console to understand a failure.

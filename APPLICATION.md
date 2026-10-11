# Token Radar local

The application uses the real project's `src/token_radar`, not the worktree.
Ralph first validates candidate changes, backs up originals, and promotes them to
that source. `auto_promote_source` is enabled. Tests establish code correctness;
they do not guarantee improved prediction or financial results.

Start from the project directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .local/supervisor-prototype/start-application.ps1
```

Application: http://127.0.0.1:8000/ . SWING: http://127.0.0.1:2226/ .
The manager polls the validated source manifest and reloads API and worker only
after promotion is complete and file hashes match. A source mismatch does not
replace the running revision. Reloading briefly interrupts collection; upstream
events during reconnection may be missed. It does not start or resume Ralph.

State: `.local/application-state.json`. Logs: `.local/logs/`.
PostgreSQL now uses 15432 and Redis 16379, both bound to loopback, retaining
the original data directories. Windows excludes the former 55432/56379 ports.
The historical scripts still reference the old ports; use this launcher for the
managed application. No Windows startup task has been installed.

Telegram delivery was explicitly requested. It activates in live mode when
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured in the project `.env`.
Missing credentials keep delivery disabled. The manager detects credential
changes and reloads the application. Credentials are not written to its state.
Existing selection/expiry/rate-limit rules remain in force. No test alert or
successful Telegram delivery is claimed before verification.

To request a reload manually, create `.local/application-reload`. To stop the
application, terminate the manager PID recorded in the state together with its
process tree (`taskkill /PID <manager_pid> /T /F` after verifying its identity).
PostgreSQL and Redis remain running to preserve their independent lifecycle.

import { NavLink, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-(--color-rule) bg-(--color-paper)">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <NavLink to="/" className="flex items-center gap-2">
            <span
              className="flex h-6 w-6 items-center justify-center rounded-sm text-xs text-white"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              ✓
            </span>
            <span className="font-display text-lg font-semibold tracking-tight text-(--color-ink)">
              DocTask <span className="text-(--color-ink-faint)">Review</span>
            </span>
          </NavLink>
          <nav className="flex gap-5 font-mono text-xs uppercase tracking-wide">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                isActive ? "text-(--color-accent)" : "text-(--color-ink-soft) hover:text-(--color-ink)"
              }
            >
              Dashboard
            </NavLink>
            <NavLink
              to="/versions"
              className={({ isActive }) =>
                isActive ? "text-(--color-accent)" : "text-(--color-ink-soft) hover:text-(--color-ink)"
              }
            >
              Version History
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}

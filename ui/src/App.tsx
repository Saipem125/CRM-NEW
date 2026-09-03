import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, explain, session } from "./api/client";
import type { Project, Role, User } from "./api/types";
import { Msg } from "./components/Common";
import LoaderScreen from "./screens/Loader";
import RunScreen from "./screens/Run";
import ResultScreen from "./screens/Result";
import WorkflowScreen from "./screens/Workflow";
import AdminScreen from "./screens/Admin";
import AdvancedScreen from "./screens/Advanced";

export interface Ctx {
  user: User;
  role: Role;
  setRole: (r: Role) => void;
  projects: Project[];
  project: Project | null;
  setProject: (p: Project | null) => void;
  refreshProjects: () => Promise<void>;
  logout: () => void;
}
export const AppCtx = createContext<Ctx | null>(null);
export const useApp = (): Ctx => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("AppCtx missing");
  return c;
};

const ROLE_RANK: Role[] = ["admin", "approver", "reviewer", "engineer", "operations", "viewer"];
export const canAct = (role: Role, ...allowed: Role[]): boolean => role === "admin" || allowed.includes(role);

function Login({ onDone }: { onDone: (u: User) => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<{ message: string; action: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const t = await api.login(u, p);
      session.token = t.access_token;
      session.actingRole = ROLE_RANK.find((r) => t.roles.includes(r)) ?? "viewer";
      onDone(await api.me());
    } catch (ex) {
      setErr(explain(ex));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <form className="card login stack" onSubmit={submit} aria-label="Sign in">
        <div className="brand"><span className="mark" /> Waterflood Optimizer</div>
        <label className="field"><span className="f">Account or e-mail</span><input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" required /></label>
        <label className="field"><span className="f">Password</span><input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></label>
        {err && <Msg severity="error" message={err.message} action={err.action} />}
        <button className="primary" disabled={busy} type="submit">{busy ? "Signing in…" : "Sign in"}</button>
        <div className="sub">No self-registration: an admin adds users by e-mail or account name (§3.1).</div>
      </form>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const [role, setRoleState] = useState<Role>((session.actingRole as Role) ?? "viewer");
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProjectState] = useState<Project | null>(null);
  const nav = useNavigate();

  const refreshProjects = useCallback(async () => {
    const ps = await api.projects.list();
    setProjects(ps);
    const saved = sessionStorage.getItem("wfo_project");
    const found = ps.find((p) => p.id === saved) ?? ps[0] ?? null;
    setProjectState(found);
  }, []);

  useEffect(() => {
    (async () => {
      if (session.token) {
        try {
          setUser(await api.me());
          await refreshProjects();
        } catch {
          session.token = null;
        }
      }
      setChecked(true);
    })();
  }, [refreshProjects]);

  const setRole = (r: Role) => { session.actingRole = r; setRoleState(r); };
  const setProject = (p: Project | null) => { p ? sessionStorage.setItem("wfo_project", p.id) : sessionStorage.removeItem("wfo_project"); setProjectState(p); };
  const logout = () => { session.token = null; setUser(null); nav("/"); };
  const ctx = useMemo<Ctx | null>(() => (user ? { user, role, setRole, projects, project, setProject, refreshProjects, logout } : null), [user, role, projects, project, refreshProjects]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!checked) return <div className="page"><div className="skeleton" style={{ height: 40, width: 320 }} /></div>;
  if (!user || !ctx) return <Login onDone={async (u) => { setUser(u); setRoleState((session.actingRole as Role) ?? "viewer"); await refreshProjects(); }} />;

  const roles = user.roles.includes("admin") ? ROLE_RANK : user.roles;
  return (
    <AppCtx.Provider value={ctx}>
      <div className="app">
        <header className="topbar">
          <div className="brand"><span className="mark" /> Waterflood Optimizer</div>
          <nav className="nav" aria-label="Main">
            <NavLink to="/load">1 · Load</NavLink>
            <NavLink to="/run">2 · Run</NavLink>
            <NavLink to="/result">3 · Result</NavLink>
            <NavLink to="/workflow">Workflow</NavLink>
            {canAct(role, "reviewer") && <NavLink to="/advanced">Advanced</NavLink>}
            {role === "admin" && <NavLink to="/admin">Admin</NavLink>}
          </nav>
          <span className="spacer" />
          <div className="userchip">
            <label className="f" htmlFor="proj">Project</label>
            <select id="proj" value={project?.id ?? ""} onChange={(e) => setProject(projects.find((p) => p.id === e.target.value) ?? null)}>
              <option value="">— none —</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.asset}</option>)}
            </select>
            <label className="f" htmlFor="role">Acting as</label>
            <select id="role" value={role} onChange={(e) => setRole(e.target.value as Role)}>{roles.map((r) => <option key={r}>{r}</option>)}</select>
            <span className="mono">{user.username}</span>
            <button className="sm ghost" onClick={logout}>Sign out</button>
          </div>
        </header>
        <main className="page">
          <Routes>
            <Route path="/" element={<Navigate to={project ? "/run" : "/load"} replace />} />
            <Route path="/load" element={<LoaderScreen />} />
            <Route path="/run" element={<RunScreen />} />
            <Route path="/result" element={<ResultScreen />} />
            <Route path="/result/:runId" element={<ResultScreen />} />
            <Route path="/workflow" element={<WorkflowScreen />} />
            <Route path="/workflow/:recId" element={<WorkflowScreen />} />
            <Route path="/advanced" element={canAct(role, "reviewer") ? <AdvancedScreen /> : <Navigate to="/run" replace />} />
            <Route path="/admin" element={role === "admin" ? <AdminScreen /> : <Navigate to="/run" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </AppCtx.Provider>
  );
}

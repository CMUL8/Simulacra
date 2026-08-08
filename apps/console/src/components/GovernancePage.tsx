import {
  ArrowLeft,
  CheckCircle2,
  Shield,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { fetchGovernance, type GovernanceOverview } from "../api";

type Props = {
  onBack: () => void;
  embedded?: boolean;
};

export function GovernancePage({ onBack, embedded = false }: Props) {
  const [data, setData] = useState<GovernanceOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchGovernance()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className={embedded ? "gov-shell gov-embedded" : "gov-shell"}>
      {!embedded && (
        <header className="gov-header">
          <button type="button" className="back-btn" onClick={onBack}>
            <ArrowLeft size={16} />
            Back
          </button>
          <div>
            <h1>Governance & Control</h1>
            <p>IT control plane for AI-generated internal apps</p>
          </div>
        </header>
      )}

      <div className="integration-banner gov-banner">
        <Shield size={20} />
        <div>
          <strong>{data?.policy.message ?? "Apps never access business systems directly."}</strong>
          <p>
            {data?.policy.description ??
              "Simulacra enforces authentication, abstracts secrets, and audits every interaction."}
          </p>
        </div>
      </div>

      {loading && <p className="gov-loading">Loading policy data…</p>}

      {data && (
        <>
          <div className="gov-stats">
            <div className="gov-stat">
              <span className="gov-stat-num">{data.summary.total_projects}</span>
              <span>Projects</span>
            </div>
            <div className="gov-stat pass">
              <span className="gov-stat-num">{data.summary.gates_pass}</span>
              <span>Gates passed</span>
            </div>
            <div className="gov-stat fail">
              <span className="gov-stat-num">{data.summary.gates_fail}</span>
              <span>Gates failed</span>
            </div>
            <div className="gov-stat">
              <span className="gov-stat-num">{data.summary.deployed}</span>
              <span>Deployed</span>
            </div>
            <div className="gov-stat">
              <span className="gov-stat-num">{data.summary.in_plan}</span>
              <span>In plan</span>
            </div>
          </div>

          <div className="gov-layer-diagram">
            <div className="layer-box user">Business teams</div>
            <div className="layer-arrow">↓ prompts</div>
            <div className="layer-box control">
              <ShieldCheck size={16} />
              Simulacra control layer
            </div>
            <div className="layer-arrow">↓ governed reads</div>
            <div className="layer-box data">Data room · APIs · MCP</div>
            <div className="layer-note">No direct system access</div>
          </div>

          <table className="gov-table">
            <thead>
              <tr>
                <th>App</th>
                <th>Phase</th>
                <th>Gates</th>
                <th>Integration</th>
                <th>Deployed</th>
                <th>Checkpoints</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => (
                <tr key={p.id}>
                  <td>
                    <strong>{p.title}</strong>
                    <span className="gov-id">{p.id}</span>
                  </td>
                  <td><span className={`phase-pill ${p.phase}`}>{p.phase}</span></td>
                  <td>
                    <span className={`gate-pill ${p.gates_status}`}>
                      {p.gates_status === "pass" ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                      {p.gates_status}
                    </span>
                  </td>
                  <td>
                    <span className="integration-pill">
                      <ShieldAlert size={12} />
                      mediated
                    </span>
                  </td>
                  <td>{p.deployed ? "✓ live" : "—"}</td>
                  <td>{p.checkpoints}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.projects.some((p) => p.gates.length > 0) && (
            <section className="gov-gates-detail">
              <h2>Gate results</h2>
              {data.projects
                .filter((p) => p.gates.length > 0)
                .map((p) => (
                  <div key={p.id} className="gov-gate-block">
                    <h3>{p.title}</h3>
                    <ul>
                      {p.gates.map((g) => (
                        <li key={g.gate} className={g.passed ? "pass" : "fail"}>
                          {g.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                          <span>{g.gate}</span>
                          <span className="gate-detail">{g.detail}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}

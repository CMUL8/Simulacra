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

      <div className="policy-hero">
        <div className="policy-hero-icon">
          <ShieldCheck size={22} strokeWidth={1.75} />
        </div>
        <div>
          <strong>{data?.policy.message ?? "Apps never access business systems directly."}</strong>
          <p>
            {data?.policy.description ??
              "Missions enforces authentication, abstracts secrets, and audits every interaction."}
          </p>
        </div>
      </div>

      {loading && <p className="gov-loading">Loading policy…</p>}

      {data && (
        <>
          <div className="policy-stats">
            <div className="policy-stat">
              <span className="policy-stat-num">{data.summary.total_projects}</span>
              <span>Projects</span>
            </div>
            <div className="policy-stat pass">
              <span className="policy-stat-num">{data.summary.gates_pass}</span>
              <span>Gates passed</span>
            </div>
            <div className="policy-stat fail">
              <span className="policy-stat-num">{data.summary.gates_fail}</span>
              <span>Gates failed</span>
            </div>
            <div className="policy-stat">
              <span className="policy-stat-num">{data.summary.deployed}</span>
              <span>Live</span>
            </div>
          </div>

          <div className="policy-flow">
            <div className="policy-flow-step">Business teams</div>
            <span className="policy-flow-arrow">prompts</span>
            <div className="policy-flow-step accent">
              <Shield size={14} />
              Control layer
            </div>
            <span className="policy-flow-arrow">governed reads</span>
            <div className="policy-flow-step">Data room · APIs</div>
          </div>

          <div className="policy-table-wrap">
            <table className="policy-table">
              <thead>
                <tr>
                  <th>App</th>
                  <th>Phase</th>
                  <th>Gates</th>
                  <th>Access</th>
                </tr>
              </thead>
              <tbody>
                {data.projects.length === 0 && (
                  <tr>
                    <td colSpan={4} className="policy-empty">
                      No projects yet
                    </td>
                  </tr>
                )}
                {data.projects.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <strong>{p.title}</strong>
                      <span className="gov-id">{p.id}</span>
                    </td>
                    <td>
                      <span className={`phase-pill ${p.phase}`}>{p.phase}</span>
                    </td>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

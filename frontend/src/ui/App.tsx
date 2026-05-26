import {
  Activity,
  AlertTriangle,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  Edit3,
  ExternalLink,
  Eye,
  FileText,
  FlaskConical,
  Gauge,
  Lock,
  LockOpen,
  Mail,
  Play,
  RefreshCcw,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Table2,
  Trash2,
  Upload,
  Zap
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  clearToken,
  deleteOffer,
  exportOffersCSV,
  fetchDashboard,
  fetchPipelineLogs,
  generateLetterForOffer,
  getStoredToken,
  login,
  rescoreOffer,
  saveOfferEmail,
  saveOfferLettre,
  saveOfferNotes,
  saveOfferStatut,
  saveOfferStatutReponse,
  saveSettings,
  sendBatch,
  sendOffer,
  testCV,
  testGmail,
  triggerFindEmails,
  triggerLetters,
  triggerNotion,
  triggerPipeline,
  triggerScore,
  uploadCV
} from "./api";
import type {
  ActionResultDto,
  BatchSendResultDto,
  DashboardDto,
  LetterPreview,
  OfferDto,
  ParametersDto,
  PipelineResultDto,
  PipelineRunDto
} from "./types";

type ViewMode = "dashboard" | "pipeline" | "responses" | "settings";
type PipelineFilter = "all" | "ready" | "missing" | "sent";
type SortMode = "score_desc" | "score_asc" | "date_desc" | "date_asc";

const STATUT_LABELS: Record<string, string> = {
  nouveau: "Nouveau",
  draft_pret: "Lettre prête",
  envoye: "Envoyé",
  ko_auto: "Rejet auto",
  ko_manuel: "Rejet manuel"
};

const STATUT_REPONSE_LABELS: Record<string, string> = {
  positif: "Réponse positive",
  negatif: "Réponse négative",
  sans_reponse: "Sans réponse",
  en_attente: "En attente"
};

function parseLetter(raw: string | null): LetterPreview | null {
  if (raw === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<LetterPreview>;
    if (typeof parsed.objet !== "string" || typeof parsed.lettre !== "string") {
      return null;
    }
    return {
      objet: parsed.objet,
      lettre: parsed.lettre,
      notes_personnalisation: Array.isArray(parsed.notes_personnalisation)
        ? parsed.notes_personnalisation.filter((note): note is string => typeof note === "string")
        : []
    };
  } catch {
    return null;
  }
}

function readyCount(offers: OfferDto[]): number {
  return offers.filter((offer) => offer.pret_envoi && offer.envoye_at === null).length;
}

function pipelineBadgeCount(offers: OfferDto[]): number {
  return offers.filter(
    (o) => o.envoye_at === null && o.statut !== "ko_auto" && o.statut !== "ko_manuel"
  ).length;
}

function hasGeneratedLetter(offer: OfferDto): boolean {
  return offer.lettre_generee !== null;
}

function isMissingSetup(offer: OfferDto): boolean {
  return offer.envoye_at === null && hasGeneratedLetter(offer) && offer.email_destinataire === null;
}

function statusLabel(statut: string): string {
  return STATUT_LABELS[statut] ?? statut;
}

export function App(): JSX.Element {
  const queryClient = useQueryClient();
  const [authenticated, setAuthenticated] = useState(() => Boolean(getStoredToken()));

  const handleLogin = async (password: string): Promise<void> => {
    await login(password);
    queryClient.clear();
    setAuthenticated(true);
  };

  const handleLogout = (): void => {
    clearToken();
    setAuthenticated(false);
  };

  if (!authenticated) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return <Dashboard onLogout={handleLogout} />;
}

function LoginScreen({ onLogin }: { onLogin: (password: string) => Promise<void> }): JSX.Element {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await onLogin(password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erreur de connexion");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <div className="brand-mark">GJ</div>
          <div>
            <p>GoodJob</p>
            <span>Tableau de bord candidatures</span>
          </div>
        </div>
        <form className="login-form" onSubmit={(e) => void handleSubmit(e)}>
          <label>
            Mot de passe
            <input
              autoFocus
              disabled={loading}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              type="password"
              value={password}
            />
          </label>
          {error && <span className="login-error">{error}</span>}
          <button className="primary-button" disabled={loading || !password} type="submit">
            {loading ? "Connexion…" : "Se connecter"}
          </button>
        </form>
      </div>
    </main>
  );
}

function Dashboard({ onLogout }: { onLogout: () => void }): JSX.Element {
  const queryClient = useQueryClient();
  const dashboardQuery = useQuery({
    queryKey: ["dashboard"],
    queryFn: fetchDashboard
  });
  const dashboard = dashboardQuery.data;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("dashboard");
  const [testMode, setTestMode] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const selectedOffer = useMemo(() => {
    if (dashboard === undefined) {
      return null;
    }
    const preferredOffer = dashboard.offers.find(
      (offer) => offer.envoye_at === null && hasGeneratedLetter(offer),
    );
    return (
      dashboard.offers.find((offer) => offer.id === selectedId) ??
      preferredOffer ??
      dashboard.offers[0] ??
      null
    );
  }, [dashboard, selectedId]);

  const replaceDashboard = (updater: (current: DashboardDto) => DashboardDto): void => {
    queryClient.setQueryData<DashboardDto>(["dashboard"], (current) => {
      if (current === undefined) {
        return current;
      }
      return updater(current);
    });
  };

  const handleOfferDeleted = (offerId: string): void => {
    setSelectedId(null);
    replaceDashboard((current) => ({
      ...current,
      offers: current.offers.filter((item) => item.id !== offerId)
    }));
  };

  const is401 =
    dashboardQuery.isError &&
    dashboardQuery.error instanceof ApiError &&
    dashboardQuery.error.status === 401;

  useEffect(() => {
    if (is401) onLogout();
  }, [is401, onLogout]);

  if (dashboardQuery.isLoading || is401) {
    return <main className="screen-state">Chargement du cockpit GoodJob...</main>;
  }

  if (dashboardQuery.isError || dashboard === undefined) {
    return (
      <main className="screen-state error">
        Impossible de charger le tableau de bord. Vérifie FastAPI et Supabase.
      </main>
    );
  }

  return (
    <main className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <aside className={`sidebar${sidebarCollapsed ? " sidebar--collapsed" : ""}`}>
        <div className="sidebar-header">
          <div className="brand">
            <div className="brand-mark">GJ</div>
            {!sidebarCollapsed && (
              <div>
                <p>GoodJob</p>
                <span>validation candidatures</span>
              </div>
            )}
          </div>
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed((v) => !v)}
            title={sidebarCollapsed ? "Déplier" : "Replier"}
            type="button"
          >
            {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        {!sidebarCollapsed && (
          <StatusPanel
            parameters={dashboard.parameters}
            offers={dashboard.offers}
            onToggleActif={() => {
              const next = { ...dashboard.parameters, actif: !dashboard.parameters.actif };
              void saveSettings(next).then((saved) =>
                replaceDashboard((c) => ({ ...c, parameters: saved }))
              );
            }}
          />
        )}

        <nav className="nav-tabs">
          <button
            className={viewMode === "dashboard" ? "active" : ""}
            onClick={() => setViewMode("dashboard")}
            title="Dashboard"
            type="button"
          >
            <Gauge size={16} />
            {!sidebarCollapsed && "Dashboard"}
          </button>
          <button
            className={viewMode === "pipeline" ? "active" : ""}
            onClick={() => setViewMode("pipeline")}
            title="Pipeline"
            type="button"
          >
            <Table2 size={16} />
            {!sidebarCollapsed && "Pipeline"}
            {pipelineBadgeCount(dashboard.offers) > 0 ? (
              <span className="nav-badge">{pipelineBadgeCount(dashboard.offers)}</span>
            ) : null}
          </button>
          <button
            className={viewMode === "responses" ? "active" : ""}
            onClick={() => setViewMode("responses")}
            title="Réponses"
            type="button"
          >
            <Activity size={16} />
            {!sidebarCollapsed && "Réponses"}
          </button>
          <button
            className={viewMode === "settings" ? "active" : ""}
            onClick={() => setViewMode("settings")}
            title="Paramètres"
            type="button"
          >
            <SlidersHorizontal size={16} />
            {!sidebarCollapsed && "Paramètres"}
          </button>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Mode production contrôlée</p>
            <h1>Chaque candidature part seulement après validation.</h1>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="ghost-button"
              disabled={dashboardQuery.isFetching}
              onClick={() => void dashboardQuery.refetch()}
              type="button"
            >
              <RefreshCcw size={16} />
              Actualiser
            </button>
            <button className="ghost-button" onClick={onLogout} title="Se déconnecter" type="button">
              <Lock size={16} />
            </button>
          </div>
        </header>

        {testMode ? (
          <div className="test-mode-banner">
            MODE TEST — envoi vers {testEmail || "(email non configuré)"}
          </div>
        ) : null}

        {viewMode === "dashboard" ? <DashboardOverview offers={dashboard.offers} /> : null}

        {viewMode === "pipeline" ? (
          <PipelineView
            offers={dashboard.offers}
            parameters={dashboard.parameters}
            selectedOffer={selectedOffer}
            testMode={testMode}
            testEmail={testEmail}
            onOfferSelected={setSelectedId}
            onOfferUpdated={(offer) =>
              replaceDashboard((current) => ({
                ...current,
                offers: current.offers.map((item) => (item.id === offer.id ? offer : item))
              }))
            }
            onOfferDeleted={handleOfferDeleted}
            onSent={() => void dashboardQuery.refetch()}
          />
        ) : null}

        {viewMode === "responses" ? (
          <ResponsesView
            offers={dashboard.offers}
            onOfferSelected={(id) => {
              setSelectedId(id);
              setViewMode("pipeline");
            }}
          />
        ) : null}

        {viewMode === "settings" ? (
          <SettingsPanel
            parameters={dashboard.parameters}
            testMode={testMode}
            testEmail={testEmail}
            onSaved={(parameters) => replaceDashboard((current) => ({ ...current, parameters }))}
            onTestModeChange={setTestMode}
            onTestEmailChange={setTestEmail}
          />
        ) : null}
      </section>
    </main>
  );
}

function DashboardOverview({ offers }: { offers: OfferDto[] }): JSX.Element {
  const sentOffers = offers.filter((offer) => offer.envoye_at !== null);
  const scoredOffers = offers.filter((offer) => offer.score_match !== null);
  const letters = offers.filter(hasGeneratedLetter);
  const missing = offers.filter(isMissingSetup);

  const missingEmail = offers.filter(
    (offer) => hasGeneratedLetter(offer) && offer.envoye_at === null && offer.email_destinataire === null
  );
  const positiveResponses = offers.filter((offer) => offer.statut_reponse === "positif");
  const responsesReceived = offers.filter(
    (offer) => offer.statut_reponse !== null && offer.statut_reponse !== "en_attente"
  ).length;
  const tauxReponse =
    sentOffers.length > 0
      ? Math.round((responsesReceived / sentOffers.length) * 100)
      : null;

  const awaitingResponse = offers.filter(
    (offer) => offer.envoye_at !== null && !offer.statut_reponse
  );

  return (
    <section className="dashboard-grid">
      <StatCard label="Offres réelles" value={offers.length} detail="dans Supabase" />
      <StatCard label="Scorées" value={scoredOffers.length} detail="score IA disponible" />
      <StatCard label="Lettres prêtes" value={letters.length} detail="générées et relues" />
      <StatCard label="Envoyées" value={sentOffers.length} detail="confirmées par Gmail" />
      <StatCard label="Emails manquants" value={missingEmail.length} detail="lettre prête, sans email" />
      <StatCard label="Réponses positives" value={positiveResponses.length} detail="employeurs intéressés" />
      <StatCard
        label="Taux de réponse"
        value={tauxReponse ?? 0}
        detail={tauxReponse === null ? "— (0 envoyé)" : `${tauxReponse}% des envois`}
      />

      <article className="wide-panel">
        <div className="section-title">
          <Clock3 size={18} />
          Email manquant
        </div>
        {missing.length === 0 ? (
          <p className="muted">Aucune candidature prête ne manque d'email recruteur.</p>
        ) : (
          <div className="todo-list">
            {missing.map((offer) => (
              <div key={offer.id}>
                <strong>{offer.titre}</strong>
                <span>{offer.entreprise ?? "Entreprise inconnue"}</span>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="wide-panel">
        <div className="section-title">
          <Mail size={18} />
          Réponses en attente
        </div>
        {awaitingResponse.length === 0 ? (
          <p className="muted">Aucune candidature envoyée sans statut de réponse.</p>
        ) : (
          <div className="todo-list">
            {awaitingResponse.map((offer) => (
              <div key={offer.id}>
                <strong>{offer.titre}</strong>
                <span>{offer.entreprise ?? "Entreprise inconnue"} · envoyé le {offer.envoye_at}</span>
              </div>
            ))}
          </div>
        )}
      </article>
    </section>
  );
}

function StatCard({
  label,
  value,
  detail
}: {
  label: string;
  value: number;
  detail: string;
}): JSX.Element {
  return (
    <article className="stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function ResponsesView({
  offers,
  onOfferSelected
}: {
  offers: OfferDto[];
  onOfferSelected: (id: string) => void;
}): JSX.Element {
  const sentOffers = offers.filter((offer) => offer.envoye_at !== null);

  const groups: { key: string; label: string; className: string; items: OfferDto[] }[] = [
    {
      key: "en_attente",
      label: "En attente",
      className: "response-en_attente",
      items: sentOffers.filter(
        (o) => o.statut_reponse === null || o.statut_reponse === "en_attente"
      )
    },
    {
      key: "positif",
      label: "Positif",
      className: "response-positif",
      items: sentOffers.filter((o) => o.statut_reponse === "positif")
    },
    {
      key: "negatif",
      label: "Négatif",
      className: "response-negatif",
      items: sentOffers.filter((o) => o.statut_reponse === "negatif")
    },
    {
      key: "sans_reponse",
      label: "Sans réponse",
      className: "response-sans_reponse",
      items: sentOffers.filter((o) => o.statut_reponse === "sans_reponse")
    }
  ];

  return (
    <section className="responses-grid">
      {sentOffers.length === 0 ? (
        <p className="muted">Aucune candidature envoyée pour l'instant.</p>
      ) : (
        groups.map((group) =>
          group.items.length > 0 ? (
            <div key={group.key} className="response-group">
              <div className="response-group-title">{group.label} ({group.items.length})</div>
              {group.items.map((offer) => (
                <button
                  key={offer.id}
                  className={`response-card ${group.className}`}
                  onClick={() => onOfferSelected(offer.id)}
                  type="button"
                >
                  <strong>{offer.titre}</strong>
                  <span>{offer.entreprise ?? "Entreprise inconnue"}</span>
                  <small>Envoyé le {offer.envoye_at}</small>
                </button>
              ))}
            </div>
          ) : null
        )
      )}
    </section>
  );
}

function PipelineView({
  offers,
  parameters,
  selectedOffer,
  testMode,
  testEmail,
  onOfferSelected,
  onOfferUpdated,
  onOfferDeleted,
  onSent
}: {
  offers: OfferDto[];
  parameters: ParametersDto;
  selectedOffer: OfferDto | null;
  testMode: boolean;
  testEmail: string;
  onOfferSelected: (offerId: string) => void;
  onOfferUpdated: (offer: OfferDto) => void;
  onOfferDeleted: (offerId: string) => void;
  onSent: () => void;
}): JSX.Element {
  const [filter, setFilter] = useState<PipelineFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("score_desc");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [search, setSearch] = useState("");

  const distinctSources = useMemo(() => {
    const sources = new Set(offers.map((o) => o.source));
    return Array.from(sources).sort();
  }, [offers]);

  const exportMutation = useMutation({
    mutationFn: exportOffersCSV
  });

  const normalizedSearch = search.trim().toLowerCase();
  const filteredOffers = useMemo(() => {
    let result = offers.filter((offer) => {
      const matchesSearch =
        normalizedSearch === "" ||
        `${offer.titre} ${offer.entreprise ?? ""} ${offer.lieu ?? ""} ${offer.source}`
          .toLowerCase()
          .includes(normalizedSearch);
      if (!matchesSearch) return false;

      if (sourceFilter !== "all" && offer.source !== sourceFilter) return false;

      if (filter === "ready") {
        return offer.envoye_at === null && hasGeneratedLetter(offer) && offer.email_destinataire !== null;
      }
      if (filter === "missing") {
        return isMissingSetup(offer);
      }
      if (filter === "sent") {
        return offer.envoye_at !== null;
      }
      return true;
    });

    result = [...result].sort((a, b) => {
      if (sortMode === "score_desc") return (b.score_match ?? -1) - (a.score_match ?? -1);
      if (sortMode === "score_asc") return (a.score_match ?? -1) - (b.score_match ?? -1);
      if (sortMode === "date_desc") {
        return (b.date_publication ?? "").localeCompare(a.date_publication ?? "");
      }
      if (sortMode === "date_asc") {
        return (a.date_publication ?? "").localeCompare(b.date_publication ?? "");
      }
      return 0;
    });

    return result;
  }, [offers, normalizedSearch, filter, sourceFilter, sortMode]);

  const highPriorityReady = filteredOffers.filter(
    (o) => (o.score_match ?? 0) >= 70 && o.lettre_generee !== null && o.email_destinataire !== null && o.envoye_at === null
  );

  const batchSendMutation = useMutation<BatchSendResultDto, Error>({
    mutationFn: () => sendBatch(highPriorityReady.map((o) => o.id)),
    onSuccess: onSent
  });

  return (
    <article className="pipeline-grid">
      <section className="pipeline-panel">
        <div className="filters-bar">
          <input
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Rechercher titre, entreprise, lieu..."
            value={search}
          />
          <select onChange={(event) => setFilter(event.target.value as PipelineFilter)} value={filter}>
            <option value="all">Toutes</option>
            <option value="ready">Prêtes à envoyer</option>
            <option value="missing">Email manquant</option>
            <option value="sent">Envoyées</option>
          </select>
          <select onChange={(event) => setSortMode(event.target.value as SortMode)} value={sortMode}>
            <option value="score_desc">Score ↓</option>
            <option value="score_asc">Score ↑</option>
            <option value="date_desc">Date ↓</option>
            <option value="date_asc">Date ↑</option>
          </select>
          <select onChange={(event) => setSourceFilter(event.target.value)} value={sourceFilter}>
            <option value="all">Toutes sources</option>
            {distinctSources.map((src) => (
              <option key={src} value={src}>{src}</option>
            ))}
          </select>
        </div>
        <div className="filters-bar-actions">
          <button
            className="ghost-button small"
            disabled={exportMutation.isPending}
            onClick={() => exportMutation.mutate()}
            type="button"
          >
            <Download size={14} />
            Exporter CSV
          </button>
        </div>
        {highPriorityReady.length > 0 && (
          <div className="batch-send-bar">
            <span>{highPriorityReady.length} offre(s) score 70+ prêtes à envoyer</span>
            <button
              className="send-button"
              disabled={batchSendMutation.isPending || !parameters.actif}
              onClick={() => batchSendMutation.mutate()}
              type="button"
            >
              <Send size={14} />
              {batchSendMutation.isPending ? "Envoi en cours…" : `Envoyer tout (${highPriorityReady.length})`}
            </button>
          </div>
        )}
        {batchSendMutation.data !== undefined && (
          <div className="batch-send-result">
            ✓ {batchSendMutation.data.sent} envoyé(s)
            {batchSendMutation.data.failed > 0 && ` · ✗ ${batchSendMutation.data.failed} échec(s)`}
          </div>
        )}
        <div className="pipeline-table">
          {filteredOffers.map((offer) => (
            <button
              className={selectedOffer?.id === offer.id ? "pipeline-item active" : "pipeline-item"}
              key={offer.id}
              onClick={() => onOfferSelected(offer.id)}
              type="button"
            >
              <span className="score">{offer.score_match ?? "—"}</span>
              <span>
                <strong>{offer.titre}</strong>
                <small>{offer.entreprise ?? "Entreprise inconnue"} · {offer.lieu ?? "Lieu non renseigné"}</small>
              </span>
              <span className={`badge badge-statut-${offer.statut}`}>{statusLabel(offer.statut)}</span>
              <span className={`badge ${offer.email_destinataire ? "badge-email-ok" : "badge-email-missing"}`}>
                {offer.email_destinataire ?? "Email manquant"}
              </span>
            </button>
          ))}
        </div>
      </section>

      {selectedOffer === null ? (
        <section className="empty-panel">Sélectionne une offre pour la gérer.</section>
      ) : (
        <OfferDetail
          offer={selectedOffer}
          parameters={parameters}
          testMode={testMode}
          testEmail={testEmail}
          onOfferUpdated={onOfferUpdated}
          onOfferDeleted={onOfferDeleted}
          onSent={onSent}
        />
      )}
    </article>
  );
}

function StatusPanel({
  parameters,
  offers,
  onToggleActif
}: {
  parameters: ParametersDto;
  offers: OfferDto[];
  onToggleActif: () => void;
}): JSX.Element {
  const ready = readyCount(offers);
  return (
    <section className={`status-panel ${parameters.actif ? "status-panel--unlocked" : "status-panel--locked"}`}>
      <div className="status-panel-top">
        <span className="status-panel-label">
          {parameters.actif ? <LockOpen size={14} /> : <Lock size={14} />}
          {parameters.actif ? "Envoi déverrouillé" : "Envoi verrouillé"}
        </span>
        <button
          className={`campaign-toggle ${parameters.actif ? "campaign-toggle--on" : "campaign-toggle--off"}`}
          onClick={onToggleActif}
          title={parameters.actif ? "Verrouiller l'envoi" : "Déverrouiller l'envoi"}
          type="button"
        >
          {parameters.actif ? "Verrouiller" : "Déverrouiller"}
        </button>
      </div>
      <div className="status-panel-bottom">
        <strong>{ready}</strong>
        <small>{ready === 1 ? "candidature prête" : "candidatures prêtes"}</small>
      </div>
    </section>
  );
}

function CvUploadZone({
  cvNom,
  cvUrl,
  onUploaded
}: {
  cvNom: string | null;
  cvUrl: string | null;
  onUploaded: (params: { cv_url: string | null; cv_nom: string | null }) => void;
}): JSX.Element {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File): Promise<void> {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Seuls les fichiers PDF sont acceptés");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setError("Le fichier dépasse la limite de 5 Mo");
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const params = await uploadCV(file);
      onUploaded({ cv_url: params.cv_url ?? null, cv_nom: params.cv_nom ?? null });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur upload");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="cv-upload-zone-wrapper">
      <label className="cv-upload-zone-label">CV (PDF)</label>
      <div
        className={`cv-upload-zone${dragOver ? " drag-over" : ""}${uploading ? " uploading" : ""}`}
        onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files[0];
          if (file) void handleFile(file);
        }}
        onClick={() => document.getElementById("cv-file-input")?.click()}
      >
        <input
          accept=".pdf,application/pdf"
          id="cv-file-input"
          style={{ display: "none" }}
          type="file"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
            e.target.value = "";
          }}
        />
        {uploading ? (
          <span className="cv-upload-hint"><RefreshCcw className="spin" size={14} /> Envoi en cours…</span>
        ) : cvNom ? (
          <span className="cv-upload-hint"><FileText size={14} /> {cvNom} — cliquer ou glisser pour remplacer</span>
        ) : (
          <span className="cv-upload-hint"><Upload size={14} /> Glisser le PDF ici ou cliquer pour choisir</span>
        )}
      </div>
      {cvUrl && (
        <a className="cv-url-link" href={cvUrl} rel="noreferrer" target="_blank">
          <ExternalLink size={12} /> Voir le CV actuel
        </a>
      )}
      {error && <span className="cv-upload-error">{error}</span>}
    </div>
  );
}

function SettingsPanel({
  parameters,
  testMode,
  testEmail,
  onSaved,
  onTestModeChange,
  onTestEmailChange
}: {
  parameters: ParametersDto;
  testMode: boolean;
  testEmail: string;
  onSaved: (parameters: ParametersDto) => void;
  onTestModeChange: (value: boolean) => void;
  onTestEmailChange: (value: string) => void;
}): JSX.Element {
  const [draft, setDraft] = useState<ParametersDto>(parameters);

  const saveMutation = useMutation({
    mutationFn: saveSettings,
    onSuccess: onSaved
  });

  const [gmailResult, setGmailResult] = useState<ActionResultDto | null>(null);
  const [cvResult, setCvResult] = useState<ActionResultDto | null>(null);
  const [scoreResult, setScoreResult] = useState<PipelineResultDto | null>(null);
  const [lettersResult, setLettersResult] = useState<PipelineResultDto | null>(null);
  const [notionResult, setNotionResult] = useState<PipelineResultDto | null>(null);
  const [emailsResult, setEmailsResult] = useState<PipelineResultDto | null>(null);
  const [triggerResult, setTriggerResult] = useState<{ ok: boolean; message: string } | null>(null);

  const gmailMutation = useMutation({
    mutationFn: testGmail,
    onSuccess: setGmailResult
  });

  const cvMutation = useMutation({
    mutationFn: testCV,
    onSuccess: setCvResult
  });

  const scoreMutation = useMutation({
    mutationFn: triggerScore,
    onSuccess: setScoreResult
  });

  const lettersMutation = useMutation({
    mutationFn: triggerLetters,
    onSuccess: setLettersResult
  });

  const notionMutation = useMutation({
    mutationFn: triggerNotion,
    onSuccess: setNotionResult
  });

  const triggerMutation = useMutation({
    mutationFn: triggerPipeline,
    onSuccess: setTriggerResult
  });

  const emailsMutation = useMutation({
    mutationFn: triggerFindEmails,
    onSuccess: setEmailsResult
  });

  const logsQuery = useQuery({
    queryKey: ["pipeline-logs"],
    queryFn: fetchPipelineLogs
  });

  const exportMutation = useMutation({
    mutationFn: exportOffersCSV
  });

  return (
    <section className="settings-strip-new">

      {/* Section 1: Paramètres d'envoi */}
      <div className="settings-section">
        <div className="settings-section-title">
          <SlidersHorizontal size={14} />
          Paramètres d'envoi
        </div>
        <p className="settings-actif-note">
          <Lock size={13} />
          Le verrou d'envoi se contrôle depuis la sidebar — chaque envoi reste manuel, offre par offre.
        </p>
        <div className="settings-row">
          <label>
            Seuil de score
            <input
              max={100}
              min={0}
              onChange={(event) => setDraft({ ...draft, score_seuil: Number(event.target.value) })}
              type="number"
              value={draft.score_seuil}
            />
          </label>
          <label>
            Max/jour
            <input
              max={50}
              min={1}
              onChange={(event) => setDraft({ ...draft, max_par_jour: Number(event.target.value) })}
              type="number"
              value={draft.max_par_jour}
            />
          </label>
        </div>
        <CvUploadZone
          cvNom={draft.cv_nom ?? null}
          cvUrl={draft.cv_url ?? null}
          onUploaded={(params) => {
            setDraft({ ...draft, cv_url: params.cv_url ?? null, cv_nom: params.cv_nom ?? null });
          }}
        />
        <button
          className="primary-button"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate(draft)}
          type="button"
        >
          <Save size={16} />
          Sauver
        </button>
        {saveMutation.isError ? <span className="inline-error">Paramètres non sauvés.</span> : null}
      </div>

      {/* Section 2: Diagnostics */}
      <div className="settings-section">
        <div className="settings-section-title">
          <FlaskConical size={14} />
          Diagnostics
        </div>
        <div className="settings-row">
          <div>
            <button
              className="ghost-button"
              disabled={gmailMutation.isPending}
              onClick={() => gmailMutation.mutate()}
              type="button"
            >
              <Mail size={14} />
              {gmailMutation.isPending ? "Test en cours…" : "Tester Gmail"}
            </button>
            {gmailResult !== null ? (
              <span className={gmailResult.ok ? "inline-ok" : "inline-error"}>
                {gmailResult.ok ? "✓" : "✗"} {gmailResult.message}
              </span>
            ) : null}
            {gmailMutation.isError ? (
              <span className="inline-error">{(gmailMutation.error as Error).message}</span>
            ) : null}
          </div>
          <div>
            <button
              className="ghost-button"
              disabled={cvMutation.isPending}
              onClick={() => cvMutation.mutate()}
              type="button"
            >
              <FileText size={14} />
              {cvMutation.isPending ? "Test en cours…" : "Tester CV"}
            </button>
            {cvResult !== null ? (
              <span className={cvResult.ok ? "inline-ok" : "inline-error"}>
                {cvResult.ok ? "✓" : "✗"} {cvResult.message}
              </span>
            ) : null}
            {cvMutation.isError ? (
              <span className="inline-error">{(cvMutation.error as Error).message}</span>
            ) : null}
          </div>
        </div>
      </div>

      {/* Section 3: Pipeline */}
      <div className="settings-section">
        <div className="settings-section-title">
          <Play size={14} />
          Pipeline
        </div>
        <div className="settings-row" style={{ marginBottom: 8 }}>
          <div>
            <button
              className="primary-button"
              disabled={triggerMutation.isPending}
              onClick={() => triggerMutation.mutate()}
              type="button"
            >
              <Play size={14} />
              {triggerMutation.isPending ? "Lancement…" : "Lancer le pipeline complet"}
            </button>
            {triggerResult !== null ? (
              <span className={triggerResult.ok ? "inline-ok" : "inline-error"}>
                {triggerResult.message}
              </span>
            ) : null}
            {triggerMutation.isError ? (
              <span className="inline-error">
                {triggerMutation.error instanceof Error ? triggerMutation.error.message : "Erreur"}
              </span>
            ) : null}
          </div>
        </div>
        <div className="settings-section-title" style={{ marginBottom: 4, fontSize: 11, color: "var(--text-muted)" }}>
          Étapes individuelles
        </div>
        <div className="settings-row">
          <div>
            <button
              className="ghost-button"
              disabled={scoreMutation.isPending}
              onClick={() => scoreMutation.mutate()}
              type="button"
            >
              <Zap size={14} />
              {scoreMutation.isPending ? "Scoring…" : "Scorer les nouvelles offres"}
            </button>
            {scoreResult !== null ? (
              <span className="inline-ok">{scoreResult.count_result} offres scorées</span>
            ) : null}
          </div>
          <div>
            <button
              className="ghost-button"
              disabled={lettersMutation.isPending}
              onClick={() => lettersMutation.mutate()}
              type="button"
            >
              <Sparkles size={14} />
              {lettersMutation.isPending ? "Génération…" : "Générer les lettres manquantes"}
            </button>
            {lettersResult !== null ? (
              <span className="inline-ok">{lettersResult.count_result} lettres générées</span>
            ) : null}
          </div>
          <div>
            <button
              className="ghost-button"
              disabled={notionMutation.isPending}
              onClick={() => notionMutation.mutate()}
              type="button"
            >
              <RotateCcw size={14} />
              {notionMutation.isPending ? "Sync…" : "Sync Notion"}
            </button>
            {notionResult !== null ? (
              <span className="inline-ok">{notionResult.count_result} pages créées</span>
            ) : null}
          </div>
          <div>
            <button
              className="ghost-button"
              disabled={emailsMutation.isPending}
              onClick={() => emailsMutation.mutate()}
              type="button"
            >
              <Mail size={14} />
              {emailsMutation.isPending ? "Recherche…" : "Trouver les emails manquants"}
            </button>
            {emailsResult !== null ? (
              <span className="inline-ok">{emailsResult.count_result} emails trouvés</span>
            ) : null}
          </div>
        </div>

        <div className="settings-section-title" style={{ marginTop: 8 }}>Derniers runs</div>
        {logsQuery.isLoading ? (
          <p className="muted">Chargement des logs…</p>
        ) : logsQuery.isError ? (
          <p className="muted">Impossible de charger les logs.</p>
        ) : (logsQuery.data ?? []).length === 0 ? (
          <p className="muted">Aucun run enregistré.</p>
        ) : (
          <div className="pipeline-logs">
            {(logsQuery.data ?? []).slice(0, 5).map((run) => (
              <div key={run.id} className="log-row">
                <span className="badge">{run.action}</span>
                <span>{run.count_result ?? "—"} résultat(s)</span>
                {run.error ? <span className="inline-error">{run.error}</span> : null}
                <span className="muted">{new Date(run.created_at).toLocaleString("fr-CH")}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section 4: Mode test */}
      <div className="settings-section">
        <div className="settings-section-title">
          <Eye size={14} />
          Mode test
        </div>
        <label className="switch-line">
          <input
            checked={testMode}
            onChange={(event) => onTestModeChange(event.target.checked)}
            type="checkbox"
          />
          Activer le mode test
        </label>
        {testMode ? (
          <label>
            Email de test
            <input
              onChange={(event) => onTestEmailChange(event.target.value)}
              placeholder="test@exemple.com"
              type="email"
              value={testEmail}
            />
          </label>
        ) : null}
      </div>

      {/* Section 5: Export */}
      <div className="settings-section">
        <div className="settings-section-title">
          <Download size={14} />
          Export
        </div>
        <button
          className="ghost-button"
          disabled={exportMutation.isPending}
          onClick={() => exportMutation.mutate()}
          type="button"
        >
          <Download size={14} />
          Exporter toutes les offres (CSV)
        </button>
      </div>

    </section>
  );
}

function OfferDetail({
  offer,
  parameters,
  testMode,
  testEmail,
  onOfferUpdated,
  onOfferDeleted,
  onSent
}: {
  offer: OfferDto;
  parameters: ParametersDto;
  testMode: boolean;
  testEmail: string;
  onOfferUpdated: (offer: OfferDto) => void;
  onOfferDeleted: (offerId: string) => void;
  onSent: () => void;
}): JSX.Element {
  const [email, setEmail] = useState(offer.email_destinataire ?? "");
  const [confirmSend, setConfirmSend] = useState(false);
  const [editingLetter, setEditingLetter] = useState(false);
  const [notes, setNotes] = useState(offer.notes ?? "");
  const [notesSaved, setNotesSaved] = useState(false);
  const letter = parseLetter(offer.lettre_generee);
  const [draftObjet, setDraftObjet] = useState(letter?.objet ?? "");
  const [draftLettre, setDraftLettre] = useState(letter?.lettre ?? "");

  const effectiveEmail = testMode ? testEmail : email.trim();

  useEffect(() => {
    setEmail(offer.email_destinataire ?? "");
    setConfirmSend(false);
    setEditingLetter(false);
    setNotes(offer.notes ?? "");
    setNotesSaved(false);
    const parsed = parseLetter(offer.lettre_generee);
    setDraftObjet(parsed?.objet ?? "");
    setDraftLettre(parsed?.lettre ?? "");
  }, [offer.id, offer.email_destinataire, offer.lettre_generee, offer.notes]);

  const emailMutation = useMutation({
    mutationFn: () => saveOfferEmail(offer.id, email.trim() === "" ? null : email.trim()),
    onSuccess: onOfferUpdated
  });

  const statutMutation = useMutation({
    mutationFn: (statut: string) => saveOfferStatut(offer.id, statut),
    onSuccess: onOfferUpdated
  });

  const statutReponseMutation = useMutation({
    mutationFn: (statut_reponse: string | null) => saveOfferStatutReponse(offer.id, statut_reponse),
    onSuccess: onOfferUpdated
  });

  const lettreMutation = useMutation({
    mutationFn: () =>
      saveOfferLettre(
        offer.id,
        draftObjet.trim(),
        draftLettre.trim(),
        letter?.notes_personnalisation ?? []
      ),
    onSuccess: (updated) => {
      onOfferUpdated(updated);
      setEditingLetter(false);
    }
  });

  const rescoreMutation = useMutation({
    mutationFn: () => rescoreOffer(offer.id),
    onSuccess: onOfferUpdated
  });

  const generateLetterMutation = useMutation({
    mutationFn: () => generateLetterForOffer(offer.id),
    onSuccess: onOfferUpdated
  });

  const notesMutation = useMutation({
    mutationFn: () => saveOfferNotes(offer.id, notes.trim() === "" ? null : notes.trim()),
    onSuccess: (updated) => {
      onOfferUpdated(updated);
      setNotesSaved(true);
      setTimeout(() => setNotesSaved(false), 2000);
    }
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteOffer(offer.id),
    onSuccess: () => onOfferDeleted(offer.id)
  });

  const sendMutation = useMutation({
    mutationFn: async () => {
      const cleanedEmail = effectiveEmail;
      if (!testMode && cleanedEmail !== (offer.email_destinataire ?? "")) {
        await saveOfferEmail(offer.id, cleanedEmail === "" ? null : cleanedEmail);
      }
      return sendOffer(offer.id);
    },
    onSuccess: () => {
      setConfirmSend(false);
      onSent();
    },
    onError: () => setConfirmSend(false)
  });

  const canSend =
    parameters.actif &&
    parameters.cv_configure &&
    offer.envoye_at === null &&
    offer.lettre_generee !== null &&
    effectiveEmail.includes("@");

  const canGenerateLetter =
    offer.score_match !== null && offer.score_match >= 65 && offer.envoye_at === null;

  const letterPreviewLines = letter?.lettre.split("\n").slice(0, 3).join("\n") ?? "";
  const letterTruncated = (letter?.lettre.split("\n").length ?? 0) > 3;

  return (
    <article className="detail-grid">
      <section className="offer-main">
        <div className="offer-heading">
          <div>
            <p className="eyebrow">{offer.entreprise ?? "Entreprise inconnue"}</p>
            <h2>{offer.titre}</h2>
            <span>{offer.lieu ?? "Lieu non renseigné"}</span>
          </div>
          <div className="offer-heading-actions">
            {offer.pret_envoi ? (
              <span className="badge-ready">✓ Prête à envoyer</span>
            ) : null}
            <a href={offer.url} rel="noreferrer" target="_blank">
              <ExternalLink size={16} />
              Offre
            </a>
            <button
              className="ghost-button danger"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (window.confirm("Supprimer définitivement cette offre ?")) {
                  deleteMutation.mutate();
                }
              }}
              type="button"
            >
              <Trash2 size={14} />
              Supprimer
            </button>
          </div>
        </div>

        <div className="score-band">
          <strong>{offer.score_match ?? "—"}</strong>
          <span>{offer.raison_score ?? "Aucune raison de score enregistrée."}</span>
          {offer.envoye_at === null ? (
            <button
              className="ghost-button small"
              disabled={rescoreMutation.isPending}
              onClick={() => rescoreMutation.mutate()}
              title="Re-scorer avec OpenAI"
              type="button"
            >
              <Zap size={14} />
              {rescoreMutation.isPending ? "Scoring…" : "Re-scorer"}
            </button>
          ) : null}
          {rescoreMutation.isError ? (
            <span className="inline-error">{String((rescoreMutation.error as Error).message)}</span>
          ) : null}
        </div>

        <div className="meta-row">
          <label>
            Statut
            <select
              disabled={statutMutation.isPending}
              onChange={(e) => statutMutation.mutate(e.target.value)}
              value={offer.statut}
            >
              {Object.entries(STATUT_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            Réponse recruteur
            <select
              disabled={statutReponseMutation.isPending}
              onChange={(e) =>
                statutReponseMutation.mutate(e.target.value === "" ? null : e.target.value)
              }
              value={offer.statut_reponse ?? ""}
            >
              <option value="">—</option>
              {Object.entries(STATUT_REPONSE_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </label>
        </div>

        <label className="email-editor">
          <Mail size={18} />
          Email recruteur validé
          <input
            onChange={(event) => setEmail(event.target.value)}
            placeholder="rh@entreprise.ch"
            type="email"
            value={email}
          />
          <button
            className="ghost-button"
            disabled={emailMutation.isPending}
            onClick={() => emailMutation.mutate()}
            type="button"
          >
            Sauver email
          </button>
        </label>

        {/* Notes personnelles */}
        <div className="notes-section">
          <label className="notes-label">Notes personnelles</label>
          <textarea
            className="notes-area"
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Ajouter une note sur cette offre..."
            value={notes}
          />
          <button
            className="ghost-button small"
            disabled={notesMutation.isPending}
            onClick={() => notesMutation.mutate()}
            type="button"
          >
            <Save size={12} />
            {notesSaved ? "Sauvé ✓" : notesMutation.isPending ? "Sauvegarde…" : "Sauver notes"}
          </button>
        </div>

        {/* Gmail / Notion IDs */}
        {(offer.gmail_message_id !== null || offer.notion_page_id !== null || offer.gmail_draft_id !== null) ? (
          <div className="offer-meta-ids">
            {offer.gmail_message_id !== null ? (
              <small>Gmail ID: {offer.gmail_message_id}</small>
            ) : null}
            {offer.gmail_draft_id !== null ? (
              <small>Draft ID: {offer.gmail_draft_id}</small>
            ) : null}
            {offer.notion_page_id !== null ? (
              <small>
                Notion:{" "}
                <a
                  href={`https://www.notion.so/${offer.notion_page_id.replace(/-/g, "")}`}
                  rel="noreferrer"
                  target="_blank"
                >
                  Voir page
                </a>
              </small>
            ) : null}
          </div>
        ) : null}

        <div className="send-panel">
          {offer.envoye_at === null ? (
            <div className="send-checklist">
              <div className={`send-check-item ${parameters.actif ? "ok" : "nok"}`}>
                {parameters.actif ? "✓" : "✗"} Gmail activé
                {!parameters.actif && <span>→ Paramètres → cocher « Gmail activé »</span>}
              </div>
              <div className={`send-check-item ${parameters.cv_configure ? "ok" : "nok"}`}>
                {parameters.cv_configure ? "✓" : "✗"} CV configuré
                {!parameters.cv_configure && <span>→ Paramètres → uploader ton CV PDF</span>}
              </div>
              <div className={`send-check-item ${offer.lettre_generee !== null ? "ok" : "nok"}`}>
                {offer.lettre_generee !== null ? "✓" : "✗"} Lettre générée
                {offer.lettre_generee === null && <span>→ cliquer « Générer la lettre » ci-dessus</span>}
              </div>
              <div className={`send-check-item ${effectiveEmail.includes("@") ? "ok" : "nok"}`}>
                {effectiveEmail.includes("@") ? "✓" : "✗"} Email recruteur renseigné
                {!effectiveEmail.includes("@") && <span>→ copier l'email depuis la page de l'offre</span>}
              </div>
              {testMode ? <Warning text={`Mode test — envoi vers ${testEmail || "(email non configuré)"}.`} /> : null}
            </div>
          ) : null}

          {confirmSend ? (
            <div className="confirm-panel">
              <div className="email-preview">
                <div className="email-preview-row">
                  <span className="email-preview-label">À :</span>
                  <span>{effectiveEmail}</span>
                </div>
                <div className="email-preview-row">
                  <span className="email-preview-label">Objet :</span>
                  <span>{letter?.objet ?? "—"}</span>
                </div>
                {letterPreviewLines ? (
                  <div className="email-preview-row">
                    <span className="email-preview-label">Corps :</span>
                    <span style={{ whiteSpace: "pre-wrap" }}>
                      {letterPreviewLines}{letterTruncated ? "\n…" : ""}
                    </span>
                  </div>
                ) : null}
                <div className="email-preview-row">
                  <span className="email-preview-label">PJ :</span>
                  <span>{parameters.cv_nom ?? "CV non configuré"}</span>
                </div>
              </div>
              <div className="confirm-actions">
                <button
                  className="send-button"
                  disabled={sendMutation.isPending}
                  onClick={() => sendMutation.mutate()}
                  type="button"
                >
                  <Send size={16} />
                  {sendMutation.isPending ? "Envoi en cours…" : "Confirmer l'envoi"}
                </button>
                <button
                  className="ghost-button"
                  disabled={sendMutation.isPending}
                  onClick={() => setConfirmSend(false)}
                  type="button"
                >
                  Annuler
                </button>
              </div>
            </div>
          ) : (
            <button
              className="send-button"
              disabled={!canSend}
              onClick={() => setConfirmSend(true)}
              type="button"
            >
              <Send size={18} />
              Envoyer cette candidature
            </button>
          )}
          {sendMutation.isError ? (
            <span className="inline-error">{String((sendMutation.error as Error).message)}</span>
          ) : null}
        </div>
      </section>

      <section className="letter-panel">
        <div className="section-title">
          <BriefcaseBusiness size={18} />
          Lettre générée
          <div className="letter-actions">
            {canGenerateLetter ? (
              <button
                className="ghost-button small"
                disabled={generateLetterMutation.isPending}
                onClick={() => generateLetterMutation.mutate()}
                title="Générer / re-générer la lettre via OpenAI"
                type="button"
              >
                <Sparkles size={14} />
                {generateLetterMutation.isPending
                  ? "Génération…"
                  : offer.lettre_generee !== null
                    ? "Re-générer"
                    : "Générer lettre"}
              </button>
            ) : null}
            {letter !== null && !editingLetter ? (
              <button
                className="ghost-button small"
                onClick={() => setEditingLetter(true)}
                type="button"
              >
                <Edit3 size={14} />
                Éditer
              </button>
            ) : null}
          </div>
        </div>
        {generateLetterMutation.isError ? (
          <span className="inline-error">
            {String((generateLetterMutation.error as Error).message)}
          </span>
        ) : null}

        {editingLetter ? (
          <div className="letter-editor">
            <label>
              Objet
              <input
                onChange={(e) => setDraftObjet(e.target.value)}
                type="text"
                value={draftObjet}
              />
            </label>
            <label>
              Corps de la lettre
              <textarea
                onChange={(e) => setDraftLettre(e.target.value)}
                rows={18}
                value={draftLettre}
              />
            </label>
            <div className="editor-actions">
              <button
                className="primary-button"
                disabled={lettreMutation.isPending}
                onClick={() => lettreMutation.mutate()}
                type="button"
              >
                <Save size={14} />
                {lettreMutation.isPending ? "Sauvegarde…" : "Sauver la lettre"}
              </button>
              <button
                className="ghost-button"
                disabled={lettreMutation.isPending}
                onClick={() => setEditingLetter(false)}
                type="button"
              >
                Annuler
              </button>
            </div>
            {lettreMutation.isError ? (
              <span className="inline-error">
                {String((lettreMutation.error as Error).message)}
              </span>
            ) : null}
          </div>
        ) : letter === null ? (
          <p className="muted">Aucune lettre prête pour cette offre.</p>
        ) : (
          <>
            <h3>{letter.objet}</h3>
            <pre>{letter.lettre}</pre>
          </>
        )}
      </section>
    </article>
  );
}

function Warning({ text }: { text: string }): JSX.Element {
  return (
    <p className="warning">
      <AlertTriangle size={16} />
      {text}
    </p>
  );
}

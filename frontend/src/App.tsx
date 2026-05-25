import {
  BarChart3,
  Bell,
  Check,
  ChevronDown,
  Download,
  Edit3,
  Eye,
  FileText,
  Gauge,
  Info,
  Lock,
  Mail,
  MoreVertical,
  Search,
  Send,
  Settings,
  SlidersHorizontal,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";

type Screen = "dashboard" | "pipeline" | "settings";
type Tone = "blue" | "green" | "red" | "orange" | "gray";

type StatCard = {
  label: string;
  value: string;
  delta: string;
  tone: Tone;
  icon: typeof BarChart3;
};

type Candidate = {
  title: string;
  company: string;
  place: string;
  source: string;
  score: number;
  pipeline: string;
  response: string;
  email: string;
  sentAt: string;
};

const stats: StatCard[] = [
  { label: "Offres trouvées", value: "142", delta: "+ 23 cette semaine", tone: "blue", icon: FileText },
  { label: "Lettres prêtes", value: "28", delta: "+ 6 cette semaine", tone: "green", icon: Lock },
  { label: "Candidatures envoyées", value: "37", delta: "+ 9 cette semaine", tone: "blue", icon: Bell },
  { label: "Réponses positives", value: "5", delta: "+ 2 cette semaine", tone: "green", icon: Check },
  { label: "Réponses négatives", value: "3", delta: "+ 1 cette semaine", tone: "red", icon: X },
  { label: "Sans réponse", value: "29", delta: "+ 6 cette semaine", tone: "blue", icon: Search },
];

const weeklyBars = [
  { day: "Lun", value: 3 },
  { day: "Mar", value: 5 },
  { day: "Mer", value: 7 },
  { day: "Jeu", value: 6 },
  { day: "Ven", value: 8 },
  { day: "Sam", value: 4 },
  { day: "Dim", value: 4 },
];

const recentApplications = [
  ["Conseiller de vente", "Manor SA, Neuchâtel", "aujourd'hui", "En attente"],
  ["Vendeur spécialisé", "Landi SA, Bienne", "hier", "En attente"],
  ["Conseiller clientèle", "Coop Suisse, Marin", "il y a 2 jours", "Sans réponse"],
  ["Vendeur conseil", "Decathlon, Moutier", "il y a 3 jours", "En attente"],
  ["Sales Advisor", "Ochsner Sport, Lyss", "il y a 3 jours", "Positif"],
];

const candidates: Candidate[] = [
  {
    title: "Conseiller de vente",
    company: "Manor SA",
    place: "Neuchâtel",
    source: "Manor",
    score: 92,
    pipeline: "Lettre prête",
    response: "En attente",
    email: "recrutement@manor.ch",
    sentAt: "-",
  },
  {
    title: "Vendeur spécialisé",
    company: "Landi SA",
    place: "Bienne",
    source: "Landi",
    score: 88,
    pipeline: "Lettre prête",
    response: "En attente",
    email: "job@landi.ch",
    sentAt: "-",
  },
  {
    title: "Sales Advisor",
    company: "Ochsner Sport",
    place: "Lyss",
    source: "Ochsner",
    score: 85,
    pipeline: "Envoyé",
    response: "Sans réponse",
    email: "hr@ochsnersport.ch",
    sentAt: "12/05/2025",
  },
  {
    title: "Conseiller clientèle",
    company: "Coop Suisse",
    place: "Marin",
    source: "Coop",
    score: 82,
    pipeline: "Envoyé",
    response: "En attente",
    email: "recrutement@coop.ch",
    sentAt: "11/05/2025",
  },
  {
    title: "Vendeur conseil",
    company: "Decathlon",
    place: "Moutier",
    source: "Decathlon",
    score: 77,
    pipeline: "Envoyé",
    response: "En attente",
    email: "recrutement@decathlon.ch",
    sentAt: "10/05/2025",
  },
  {
    title: "Conseiller de vente",
    company: "Fust",
    place: "Bienne",
    source: "Fust",
    score: 74,
    pipeline: "Scoré",
    response: "En attente",
    email: "job@fust.ch",
    sentAt: "-",
  },
  {
    title: "Vendeur",
    company: "Jumbo",
    place: "Neuchâtel",
    source: "Jumbo",
    score: 72,
    pipeline: "Lettre prête",
    response: "En attente",
    email: "recrutement@jumbo.ch",
    sentAt: "-",
  },
  {
    title: "Conseiller de vente",
    company: "Micropot",
    place: "Marin",
    source: "Micropot",
    score: 69,
    pipeline: "Scoré",
    response: "En attente",
    email: "jobs@micropot.ch",
    sentAt: "-",
  },
  {
    title: "Télévendeur",
    company: "Salt Store",
    place: "Neuchâtel",
    source: "Salt",
    score: 65,
    pipeline: "Rejeté automatiquement",
    response: "-",
    email: "job@salt.ch",
    sentAt: "-",
  },
  {
    title: "Sales Consultant",
    company: "Sunrise",
    place: "Bienne",
    source: "Sunrise",
    score: 61,
    pipeline: "Rejeté automatiquement",
    response: "-",
    email: "careers@sunrise.net",
    sentAt: "-",
  },
];

const menu = [
  { id: "dashboard", label: "Dashboard", icon: Gauge },
  { id: "pipeline", label: "Pipeline", icon: SlidersHorizontal },
  { id: "settings", label: "Paramètres", icon: Settings },
] satisfies Array<{ id: Screen; label: string; icon: typeof Gauge }>;

function App() {
  const [screen, setScreen] = useState<Screen>("dashboard");
  const title = useMemo(() => menu.find((item) => item.id === screen)?.label ?? "Dashboard", [screen]);

  return (
    <div className="app-shell">
      <Sidebar activeScreen={screen} onNavigate={setScreen} />
      <main className={`page page-${screen}`}>
        <header className="page-header">
          <h1>{title}</h1>
        </header>
        {screen === "dashboard" && <Dashboard />}
        {screen === "pipeline" && <Pipeline />}
        {screen === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}

function Sidebar({ activeScreen, onNavigate }: { activeScreen: Screen; onNavigate: (screen: Screen) => void }) {
  return (
    <aside className="sidebar">
      <div className="brand">GoodJob</div>
      <nav className="main-nav">
        {menu.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className={`nav-item ${activeScreen === item.id ? "is-active" : ""}`}
              key={item.id}
              onClick={() => onNavigate(item.id)}
              type="button"
            >
              <Icon size={16} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="profile">
        <div className="avatar">BH</div>
        <div>
          <strong>Bryan Hilaire</strong>
          <span>bryan.hilaire@gmail.com</span>
        </div>
      </div>
    </aside>
  );
}

function Dashboard() {
  return (
    <div className="dashboard-grid">
      <section className="stats-grid">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <article className="stat-card" key={stat.label}>
              <div>
                <p>{stat.label}</p>
                <strong>{stat.value}</strong>
                <span className="delta">+ {stat.delta.replace("+ ", "")}</span>
              </div>
              <div className={`round-icon tone-${stat.tone}`}>
                <Icon size={18} />
              </div>
            </article>
          );
        })}
      </section>

      <section className="panel chart-panel">
        <h2>Candidatures envoyées cette semaine</h2>
        <div className="bar-chart">
          {weeklyBars.map((bar) => (
            <div className="bar-column" key={bar.day}>
              <span>{bar.value}</span>
              <div style={{ height: `${bar.value * 18}px` }} />
              <small>{bar.day}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="panel recent-panel">
        <div className="panel-title-row">
          <h2>Dernières candidatures envoyées</h2>
          <button type="button">Voir tout</button>
        </div>
        <div className="recent-list">
          {recentApplications.map(([title, company, date, status]) => (
            <div className="recent-item" key={`${title}-${company}`}>
              <div>
                <strong>{title}</strong>
                <span>{company}</span>
              </div>
              <time>{date}</time>
              <Badge label={status} />
            </div>
          ))}
        </div>
      </section>

      <SystemState />
      <div className="notice notice-blue">
        <Info size={16} />
        <span>Aucune candidature n'est envoyée automatiquement. Vous gardez le contrôle.</span>
      </div>
    </div>
  );
}

function SystemState() {
  const items = [
    ["Automation", "Active"],
    ["Score minimum", "70 / 100"],
    ["Envois par jour (max)", "10"],
    ["CV configuré", "CV_Bryan_Hilaire.pdf"],
    ["Gmail", "Connecté"],
    ["Supabase", "Connecté"],
  ];

  return (
    <section className="panel system-panel">
      <h2>État du système</h2>
      <div className="system-grid">
        {items.map(([label, value]) => (
          <div className="system-item" key={label}>
            <span>{label}</span>
            <strong>
              <Check size={14} />
              {value}
            </strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function Pipeline() {
  return (
    <div className="pipeline-layout">
      <section className="pipeline-main">
        <div className="filters">
          <SelectLabel label="Statut pipeline" value="Tous" />
          <SelectLabel label="Statut réponse" value="Tous" />
          <SelectLabel label="Score minimum" value="70" />
          <label className="search-field">
            <Search size={15} />
            <input placeholder="Rechercher (titre, entreprise, lieu...)" />
          </label>
          <button className="primary-button" type="button">
            Envoyer tout (6)
            <Send size={15} />
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Titre du poste</th>
                <th>Entreprise</th>
                <th>Lieu</th>
                <th>Source</th>
                <th>Score IA</th>
                <th>Statut pipeline</th>
                <th>Statut réponse</th>
                <th>Email recruteur</th>
                <th>Date d'envoi</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr key={`${candidate.title}-${candidate.company}`}>
                  <td>{candidate.title}</td>
                  <td>{candidate.company}</td>
                  <td>{candidate.place}</td>
                  <td>{candidate.source}</td>
                  <td className="score-cell">{candidate.score}</td>
                  <td>
                    <Badge label={candidate.pipeline} />
                  </td>
                  <td>
                    <Badge label={candidate.response} />
                  </td>
                  <td>{candidate.email}</td>
                  <td>{candidate.sentAt}</td>
                  <td>
                    <div className="action-icons">
                      <button type="button" aria-label="Voir">
                        <Eye size={14} />
                      </button>
                      <button type="button" aria-label="Plus">
                        <MoreVertical size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <aside className="detail-panel">
        <button className="close-button" type="button" aria-label="Fermer">
          <X size={16} />
        </button>
        <h2>Conseiller de vente</h2>
        <div className="mini-meta">
          <span>Manor SA</span>
          <span>Neuchâtel</span>
        </div>
        <div className="score-badge">92 / 100</div>
        <span className="score-label">Score IA</span>
        <DetailBlock title="Raison du score">
          Expérience en vente adaptée, secteur retail, proche du lieu, compétences relationnelles match.
        </DetailBlock>
        <DetailBlock title="Description brute">
          Manor Neuchâtel recherche un conseiller.ère de vente pour rejoindre son équipe. Vous conseillez et accompagnez les clients...
          <button type="button">Voir plus</button>
        </DetailBlock>
        <DetailBlock title="Lettre générée">
          Bonjour,
          <br />
          <br />
          Je me permets de vous adresser ma candidature pour le poste de conseiller de vente au sein de Manor Neuchâtel...
          <button type="button">Voir la lettre complète</button>
        </DetailBlock>
        <label className="field-label">
          Email recruteur
          <span className="input-with-icon">
            <input value="recrutement@manor.ch" readOnly />
            <Edit3 size={15} />
          </span>
        </label>
        <label className="field-label">
          Lien vers l'offre
          <span className="input-with-icon link-input">
            <input value="http://manor.ch/emploi/conseiller-vente" readOnly />
            <Download size={15} />
          </span>
        </label>
        <button className="send-candidate" type="button">
          Envoyer cette candidature
          <Send size={16} />
        </button>
      </aside>
    </div>
  );
}

function SettingsPage() {
  return (
    <div className="settings-grid">
      <section className="panel settings-card general-card">
        <h2>Configuration générale</h2>
        <div className="setting-row">
          <div>
            <strong>Automation active</strong>
            <span>Active ou désactive la recherche et la préparation des candidatures.</span>
          </div>
          <button className="toggle is-on" type="button" aria-label="Automation active" />
        </div>
        <SliderField label="Nombre maximum d'envois par jour" value="10" marks={["1", "5", "10", "20", "30"]} progress={48} />
        <SliderField label="Score minimum pour générer/envoyer une lettre" value="70" marks={["0", "25", "50", "75", "100"]} progress={72} />
      </section>

      <section className="panel settings-card">
        <h2>CV</h2>
        <strong className="subheading">CV actuel</strong>
        <div className="file-card">
          <FileText size={34} />
          <div>
            <strong>CV_Bryan_Hilaire.pdf</strong>
            <span>245 Ko · Ajouté le 10/05/2025</span>
          </div>
          <Badge label="Configuré" />
        </div>
        <button className="secondary-button" type="button">
          <Upload size={15} />
          Remplacer le CV
        </button>
        <div className="notice notice-blue cv-notice">
          <Info size={15} />
          <span>Votre CV sera joint à chaque candidature envoyée.</span>
        </div>
      </section>

      <section className="panel settings-card connections-card">
        <h2>Connexions</h2>
        <Connection name="Gmail" detail="bryan.hilaire@gmail.com" variant="gmail" />
        <Connection name="Supabase" detail="Projet GoodJob" variant="supabase" />
      </section>

      <section className="panel settings-card summary-card">
        <h2>Récapitulatif</h2>
        {[
          ["Automation", "Active"],
          ["Max envois / jour", "10"],
          ["Score minimum", "70"],
          ["CV", "CV_Bryan_Hilaire.pdf"],
          ["Gmail", "Connecté"],
          ["Supabase", "Connecté"],
        ].map(([label, value]) => (
          <div className="summary-row" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </section>

      <button className="save-button" type="button">
        <Lock size={16} />
        Sauvegarder les paramètres
      </button>
      <div className="notice notice-yellow">
        <Bell size={16} />
        <span>Aucun envoi automatique. Vous devez toujours valider l'envoi manuellement.</span>
      </div>
    </div>
  );
}

function SelectLabel({ label, value }: { label: string; value: string }) {
  return (
    <label className="select-label">
      <span>{label}</span>
      <button type="button">
        {value}
        <ChevronDown size={14} />
      </button>
    </label>
  );
}

function Badge({ label }: { label: string }) {
  const normalized = label.toLowerCase().replace(/\s/g, "-");
  return <span className={`badge badge-${normalized}`}>{label}</span>;
}

function DetailBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="detail-block">
      <h3>{title}</h3>
      <p>{children}</p>
    </section>
  );
}

function SliderField({
  label,
  value,
  marks,
  progress,
}: {
  label: string;
  value: string;
  marks: string[];
  progress: number;
}) {
  return (
    <div className="slider-field">
      <strong>{label}</strong>
      <div className="slider-line">
        <span className="number-box">{value}</span>
        <div className="track">
          <span style={{ width: `${progress}%` }} />
        </div>
      </div>
      <div className="marks">
        {marks.map((mark) => (
          <span key={mark}>{mark}</span>
        ))}
      </div>
    </div>
  );
}

function Connection({ name, detail, variant }: { name: string; detail: string; variant: "gmail" | "supabase" }) {
  return (
    <div className="connection">
      <div className={`provider-logo provider-${variant}`}>{variant === "gmail" ? "M" : <Zap size={34} />}</div>
      <div>
        <h3>{name}</h3>
        <strong>Connecté</strong>
        <span>{detail}</span>
      </div>
      <button type="button">Déconnecter</button>
    </div>
  );
}

export default App;

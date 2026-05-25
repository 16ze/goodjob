import type {
  ActionResultDto,
  BatchSendResultDto,
  DashboardDto,
  OfferDto,
  ParametersDto,
  PipelineResultDto,
  PipelineRunDto,
  SendResultDto
} from "./types";

const TOKEN_KEY = "gj_token";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeader(): Record<string, string> {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type ErrorPayload = {
  detail?: string;
};

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as unknown;
  if (!response.ok) {
    const errorPayload = payload as ErrorPayload;
    throw new ApiError(errorPayload.detail ?? "Erreur API GoodJob", response.status);
  }
  return payload as T;
}

function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  return fetch(url, {
    ...init,
    headers: {
      ...authHeader(),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
}

export async function login(password: string): Promise<string> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const data = await readJson<{ token: string }>(response);
  storeToken(data.token);
  return data.token;
}

export async function fetchDashboard(): Promise<DashboardDto> {
  const response = await apiFetch("/api/dashboard");
  return readJson<DashboardDto>(response);
}

export async function saveSettings(parameters: ParametersDto): Promise<ParametersDto> {
  const response = await apiFetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      actif: parameters.actif,
      max_par_jour: parameters.max_par_jour,
      score_seuil: parameters.score_seuil,
      cv_url: parameters.cv_url,
      cv_nom: parameters.cv_nom
    })
  });
  return readJson<ParametersDto>(response);
}

export async function saveOfferEmail(
  offerId: string,
  email: string | null,
): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/email`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email_destinataire: email })
  });
  return readJson<OfferDto>(response);
}

export async function sendOffer(offerId: string): Promise<SendResultDto> {
  const response = await apiFetch(`/api/offers/${offerId}/send`, { method: "POST" });
  return readJson<SendResultDto>(response);
}

export async function saveOfferStatut(offerId: string, statut: string): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/statut`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ statut })
  });
  return readJson<OfferDto>(response);
}

export async function saveOfferStatutReponse(
  offerId: string,
  statut_reponse: string | null,
): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/statut_reponse`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ statut_reponse })
  });
  return readJson<OfferDto>(response);
}

export async function saveOfferLettre(
  offerId: string,
  objet: string,
  lettre: string,
  notes_personnalisation: string[],
): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/lettre`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ objet, lettre, notes_personnalisation })
  });
  return readJson<OfferDto>(response);
}

export async function rescoreOffer(offerId: string): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/rescore`, { method: "POST" });
  return readJson<OfferDto>(response);
}

export async function generateLetterForOffer(offerId: string): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/generate_letter`, { method: "POST" });
  return readJson<OfferDto>(response);
}

export async function saveOfferNotes(offerId: string, notes: string | null): Promise<OfferDto> {
  const response = await apiFetch(`/api/offers/${offerId}/notes`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes })
  });
  return readJson<OfferDto>(response);
}

export async function deleteOffer(offerId: string): Promise<void> {
  const response = await apiFetch(`/api/offers/${offerId}`, { method: "DELETE" });
  if (!response.ok) {
    const payload = (await response.json()) as ErrorPayload;
    throw new ApiError(payload.detail ?? "Erreur suppression", response.status);
  }
}

export async function exportOffersCSV(): Promise<void> {
  const response = await apiFetch("/api/offers/export");
  if (!response.ok) {
    throw new ApiError("Erreur export CSV", response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "offres.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function uploadCV(file: File): Promise<ParametersDto> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await apiFetch("/api/settings/upload_cv", { method: "POST", body: form });
  return readJson<ParametersDto>(response);
}

export async function testGmail(): Promise<ActionResultDto> {
  const response = await apiFetch("/api/settings/test_gmail", { method: "POST" });
  return readJson<ActionResultDto>(response);
}

export async function testCV(): Promise<ActionResultDto> {
  const response = await apiFetch("/api/settings/test_cv");
  return readJson<ActionResultDto>(response);
}

export async function triggerScore(): Promise<PipelineResultDto> {
  const response = await apiFetch("/api/pipeline/score", { method: "POST" });
  return readJson<PipelineResultDto>(response);
}

export async function triggerLetters(): Promise<PipelineResultDto> {
  const response = await apiFetch("/api/pipeline/letters", { method: "POST" });
  return readJson<PipelineResultDto>(response);
}

export async function triggerNotion(): Promise<PipelineResultDto> {
  const response = await apiFetch("/api/pipeline/notion", { method: "POST" });
  return readJson<PipelineResultDto>(response);
}

export async function fetchPipelineLogs(): Promise<PipelineRunDto[]> {
  const response = await apiFetch("/api/pipeline/logs");
  return readJson<PipelineRunDto[]>(response);
}

export async function sendBatch(offerIds: string[]): Promise<BatchSendResultDto> {
  const response = await apiFetch("/api/offers/send_batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ offer_ids: offerIds })
  });
  return readJson<BatchSendResultDto>(response);
}

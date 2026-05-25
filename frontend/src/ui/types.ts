export type ParametersDto = {
  actif: boolean;
  max_par_jour: number;
  score_seuil: number;
  cv_url: string | null;
  cv_nom: string | null;
  cv_configure: boolean;
};

export type OfferDto = {
  id: string;
  source: string;
  titre: string;
  entreprise: string | null;
  lieu: string | null;
  code_postal: string | null;
  date_publication: string | null;
  url: string;
  description_brute: string | null;
  score_match: number | null;
  raison_score: string | null;
  statut: string;
  statut_reponse: string | null;
  lettre_generee: string | null;
  email_destinataire: string | null;
  envoye_at: string | null;
  pret_envoi: boolean;
  notes: string | null;
  gmail_message_id: string | null;
  notion_page_id: string | null;
  gmail_draft_id: string | null;
};

export type DashboardDto = {
  parameters: ParametersDto;
  offers: OfferDto[];
};

export type SendResultDto = {
  message_id: string;
  thread_id: string | null;
};

export type LetterPreview = {
  objet: string;
  lettre: string;
  notes_personnalisation: string[];
};

export type PipelineRunDto = {
  id: number;
  action: string;
  count_result: number | null;
  error: string | null;
  created_at: string;
};

export type ActionResultDto = {
  ok: boolean;
  message: string;
  url?: string | null;
};

export type PipelineResultDto = {
  count_result: number;
};

export type BatchSendResultDto = {
  sent: number;
  failed: number;
  errors: string[];
};

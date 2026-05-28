"""
Recherche d'emails de recrutement — stratégie gratuite, 4 niveaux.

Niveau 1 : emails connus en dur (grandes enseignes suisses)
Niveau 2 : scraping de la page jobup/jobboard → extrait le lien du site entreprise
Niveau 3 : scraping du site entreprise (homepage + pages carrières)
Niveau 4 : heuristique domaine à partir du nom (plusieurs variantes)
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog

log = structlog.get_logger()

# ── Emails connus en dur ──────────────────────────────────────────────────────

KNOWN_EMAILS: dict[str, str] = {
    # Grande distribution
    "migros":    "jobs@mgb.ch",
    "coop":      "personal@coop.ch",
    "lidl":      "recrutement@lidl.ch",
    "aldi":      "recrutement@aldi.ch",
    "manor":     "rh@manor.ch",
    "denner":    "personal@denner.ch",
    "landi":     "personal@fenaco.com",
    "spar":      "rh@spar.ch",
    "volg":      "personal@volg.ch",
    "aldi suisse": "recrutement@aldi.ch",
    # Télécom
    "salt":      "rh@salt.ch",
    "sunrise":   "rh@sunrise.ch",
    "swisscom":  "jobs@swisscom.com",
    "mobilezone": "jobs@mobilezone.ch",
    # Horlogerie / bijouterie
    "swatch":    "jobs@swatchgroup.com",
    "omega":     "jobs@omegawatches.com",
    "tissot":    "jobs@tissot.ch",
    "festina":   "rh@festina.com",
    # Finance
    "bcj":       "rh@bcj.ch",
    "bcn":       "rh@bcn.ch",
    "bcbe":      "emplois@bcbe.ch",
    "banque cantonale du jura": "rh@bcj.ch",
    "banque cantonale de neuchâtel": "rh@bcn.ch",
    "banque cantonale bernoise": "emplois@bcbe.ch",
    "credit suisse": "recruiting@credit-suisse.com",
    "ubs":       "recruiting@ubs.com",
    "raiffeisen": "jobs@raiffeisen.ch",
    "postfinance": "jobs@postfinance.ch",
    # Divers Jura / Neuchâtel
    "gvd":       "rh@gvd.ch",
    "lemo":      "jobs@lemo.com",
    "mido":      "jobs@mido.com",
    "focuslight": "hr@focuslight.com",
}

# ── Suffixes légaux à supprimer du nom d'entreprise ───────────────────────────

_LEGAL_SUFFIXES = re.compile(
    r"\b(s\.?a\.?r?\.?l?\.?|gmbh|ag|ltd|inc|sas|group|holding|suisse|schweiz|sa)\b",
    re.IGNORECASE,
)

# ── Chemins à scraper sur le site de l'entreprise ────────────────────────────

_CAREER_PATHS = [
    "",
    "/carrieres", "/careers", "/emplois", "/jobs", "/recrutement",
    "/offres-emploi", "/nous-rejoindre", "/postes-ouverts",
    "/contact", "/a-propos/contact", "/fr/contact", "/de/kontakt",
    "/about/contact", "/about-us/contact",
]

# ── Mots-clés qui signalent un email de contact/recrutement ──────────────────

_CONTACT_KEYWORDS = {
    "rh", "hr", "recrutement", "recruitment", "candidature", "candidatures",
    "bewerbung", "jobs", "job", "career", "careers", "emploi", "emplois",
    "personal", "people", "talent", "talents", "contact", "info",
    "postulation", "postuler", "apply",
}

# ── Domaines de jobboards/tiers à ignorer ────────────────────────────────────

_IGNORE_DOMAINS = {
    "jobup.ch", "jobs.ch", "indeed.com", "linkedin.com", "xing.com",
    "noreply", "no-reply", "example.com", "test.com", "placeholder",
    "sentry.io", "google.com", "microsoft.com", "apple.com", "w3.org",
    "schema.org", "datatables.net", "jquery.com",
}

# ── Regex email ───────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# ── Regex pour trouver un lien vers le site entreprise dans une page jobboard ─

_COMPANY_URL_RE = re.compile(
    r'(?:site[- ]web|website|entreprise|company|url)["\s:>]*'
    r'(https?://[^\s"\'<>]+)',
    re.IGNORECASE,
)

_HREF_RE = re.compile(r'href=["\']?(https?://[^\s"\'<>]+)', re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_good_email(email: str) -> bool:
    """Accepte les emails de contact/recrutement, rejette les faux positifs."""
    email = email.lower()
    domain = email.split("@")[1] if "@" in email else ""
    local  = email.split("@")[0]

    if any(bl in domain for bl in _IGNORE_DOMAINS):
        return False
    # Rejette extensions improbables
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) > 6:
        return False
    # Doit contenir un mot-clé OU être un email court générique d'entreprise
    if any(kw in local for kw in _CONTACT_KEYWORDS):
        return True
    # Email court (< 12 chars local) sur un domaine .ch/.com probable = acceptable
    if len(local) <= 12 and tld in {"ch", "com", "net", "org", "swiss"}:
        return True
    return False


def _extract_emails(html: str) -> list[str]:
    """Extrait et classe les emails d'une page HTML."""
    raw = _EMAIL_RE.findall(html)
    seen: dict[str, int] = {}
    for e in raw:
        e_low = e.lower()
        seen[e_low] = seen.get(e_low, 0) + 1

    good    = [e for e in seen if _is_good_email(e)]
    generic = [e for e in seen if e not in good and "@" in e
               and not any(bl in e for bl in _IGNORE_DOMAINS)]

    # Priorité : mots-clés RH explicites d'abord, puis fréquence
    rh_first = sorted(good,    key=lambda e: (0 if any(k in e for k in {"rh","hr","recrutement","jobs","career"}) else 1, -seen[e]))
    others   = sorted(generic, key=lambda e: -seen[e])
    return rh_first + others


def _clean_company_name(name: str) -> str:
    """Retire les suffixes légaux : 'LEMO SA' → 'lemo', 'MIDO AG' → 'mido'."""
    n = _LEGAL_SUFFIXES.sub(" ", name.lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return " ".join(n.split())


def _domain_candidates(entreprise: str) -> list[str]:
    """
    Génère plusieurs variantes de domaine à tester pour une entreprise.
    Ex: 'Banque Cantonale du Jura SA' →
        ['banquecantoneledujura.ch', 'banque.ch', 'bcj.ch'] (heuristique)
    """
    clean = _clean_company_name(entreprise)
    words = clean.split()
    if not words:
        return []

    candidates: list[str] = []
    # Nom complet sans espaces
    full = "".join(words)
    if 3 <= len(full) <= 24:
        candidates.append(f"https://{full}.ch")
        candidates.append(f"https://{full}.com")

    # Premier mot seul (souvent la marque)
    first = words[0]
    if len(first) >= 3 and first != full:
        candidates.append(f"https://{first}.ch")
        candidates.append(f"https://{first}.com")

    # Deux premiers mots
    if len(words) >= 2:
        two = words[0] + words[1]
        if 3 <= len(two) <= 20 and two not in {full, first}:
            candidates.append(f"https://{two}.ch")

    # Acronyme (initiales si nom long)
    if len(words) >= 3:
        acronym = "".join(w[0] for w in words if w)
        if 2 <= len(acronym) <= 5:
            candidates.append(f"https://{acronym}.ch")

    return list(dict.fromkeys(candidates))  # dédupliquer en gardant l'ordre


def _extract_company_site_from_jobboard(html: str, board_host: str) -> str | None:
    """
    Cherche le lien vers le site de l'entreprise dans une page de jobboard.
    Retourne le domaine racine si trouvé (ex: 'https://lemo.com').
    """
    # 1. Cherche un champ site web explicite
    m = _COMPANY_URL_RE.search(html)
    if m:
        url = m.group(1).rstrip("/?&")
        host = urlparse(url).netloc.lower()
        if host and board_host not in host:
            return f"https://{host}"

    # 2. Cherche des hrefs vers des domaines externes
    for href in _HREF_RE.findall(html):
        try:
            parsed = urlparse(href)
            host = parsed.netloc.lower()
            if not host or board_host in host:
                continue
            if any(bl in host for bl in _IGNORE_DOMAINS):
                continue
            # Doit ressembler à un site d'entreprise (pas juste google/social)
            if any(s in host for s in {"google.", "facebook.", "twitter.", "instagram.", "linkedin.", "youtube.", "apple.", "microsoft."}):
                continue
            # Prend le premier lien externe non-suspect
            return f"https://{host}"
        except Exception:
            continue
    return None


# ── Fonction principale ───────────────────────────────────────────────────────

async def find_company_email(
    entreprise: str,
    url: str,
    *,
    timeout: float = 10.0,
) -> str | None:
    """
    Stratégie en 4 niveaux pour trouver l'email de recrutement d'une entreprise.
    """
    if not entreprise:
        return None

    # ── Niveau 1 : emails connus en dur ──────────────────────────────────────
    name_lower = entreprise.lower().strip()
    for keyword, email in KNOWN_EMAILS.items():
        if keyword in name_lower:
            log.info("email_finder.known", entreprise=entreprise, email=email)
            return email

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=timeout,
        follow_redirects=True,
        verify=False,
    ) as client:

        # ── Niveau 2 : scrape la page jobboard pour trouver le site entreprise ──
        company_site: str | None = None
        if url:
            try:
                board_host = urlparse(url).netloc.lower()
                resp = await client.get(url)
                if resp.status_code == 200:
                    # Cherche email directement sur la page jobboard
                    emails = _extract_emails(resp.text)
                    if emails:
                        log.info("email_finder.from_jobboard_page",
                                 entreprise=entreprise, email=emails[0])
                        return emails[0]
                    # Sinon cherche le site de l'entreprise
                    company_site = _extract_company_site_from_jobboard(resp.text, board_host)
                    if company_site:
                        log.info("email_finder.company_site_found",
                                 entreprise=entreprise, site=company_site)
            except Exception as exc:
                log.debug("email_finder.jobboard_error", url=url, error=str(exc))

        # ── Niveaux 3 & 4 : sites à tester ────────────────────────────────────
        sites_to_try: list[str] = []
        if company_site:
            sites_to_try.append(company_site)          # site trouvé sur jobboard
        sites_to_try.extend(_domain_candidates(entreprise))  # heuristiques

        for base_url in sites_to_try:
            for path in _CAREER_PATHS:
                page_url = base_url.rstrip("/") + path
                try:
                    resp = await client.get(page_url)
                    if resp.status_code not in (200, 201):
                        continue
                    emails = _extract_emails(resp.text)
                    if emails:
                        log.info(
                            "email_finder.found",
                            entreprise=entreprise,
                            page=page_url,
                            email=emails[0],
                        )
                        return emails[0]
                except Exception as exc:
                    log.debug("email_finder.page_error",
                              url=page_url, error=str(exc))
                    continue
            # Si la homepage répond on ne teste qu'elle + /contact, pas tout
            # (évite de spammer des centaines de requêtes)

    log.info("email_finder.not_found", entreprise=entreprise)
    return None


def find_company_email_sync(
    entreprise: str,
    url: str,
    *,
    timeout: float = 10.0,
) -> str | None:
    """Version synchrone — utilisée dans le pipeline CLI."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    find_company_email(entreprise, url, timeout=timeout),
                )
                return future.result(timeout=timeout + 2)
        return loop.run_until_complete(
            find_company_email(entreprise, url, timeout=timeout)
        )
    except Exception as exc:
        log.debug("email_finder.sync_error", entreprise=entreprise, error=str(exc))
        return None

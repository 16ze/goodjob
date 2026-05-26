"""
Recherche d'emails de recrutement sur les sites entreprises.
Stratégie 100% gratuite : scraping httpx + regex + patterns connus.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog

log = structlog.get_logger()

# Emails connus pour les grandes enseignes suisses ciblées
KNOWN_EMAILS: dict[str, str] = {
    "migros": "jobs@mgb.ch",
    "coop": "personal@coop.ch",
    "lidl": "recrutement@lidl.ch",
    "aldi": "recrutement@aldi.ch",
    "manor": "rh@manor.ch",
    "denner": "personal@denner.ch",
    "landi": "personal@fenaco.com",
    "spar": "rh@spar.ch",
    "salt": "rh@salt.ch",
    "sunrise": "rh@sunrise.ch",
    "mobilezone": "jobs@mobilezone.ch",
}

# Sous-pages à scraper sur le site de l'entreprise
CAREER_PATHS = [
    "/carrieres", "/careers", "/emplois", "/jobs",
    "/recrutement", "/offres-emploi", "/nous-rejoindre",
    "/contact", "/a-propos/contact", "/fr/contact",
]

# Regex email générique (exclut les faux positifs courants)
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Mots-clés qui signalent un email RH/recrutement
_HR_KEYWORDS = {
    "rh", "hr", "recrutement", "recruitment", "candidature",
    "jobs", "career", "emploi", "personal", "people",
}

# Domaines à ignorer (noreply, plateformes tierces, etc.)
_BLOCKLIST_DOMAINS = {
    "jobup.ch", "jobs.ch", "indeed.com", "linkedin.com",
    "noreply", "no-reply", "example.com", "test.com",
    "sentry.io", "google.com", "microsoft.com",
}


def _is_hr_email(email: str) -> bool:
    local = email.split("@")[0].lower()
    domain = email.split("@")[1].lower() if "@" in email else ""
    if any(bl in domain for bl in _BLOCKLIST_DOMAINS):
        return False
    return any(kw in local for kw in _HR_KEYWORDS)


def _extract_emails_from_html(html: str) -> list[str]:
    """Extrait et trie les emails trouvés dans une page HTML."""
    raw = _EMAIL_RE.findall(html)
    seen: dict[str, int] = {}
    for e in raw:
        e_low = e.lower()
        seen[e_low] = seen.get(e_low, 0) + 1

    # Priorise les emails RH, puis trie par fréquence
    hr = [e for e in seen if _is_hr_email(e)]
    others = [e for e in seen if not _is_hr_email(e) and any(bl not in e for bl in _BLOCKLIST_DOMAINS)]

    return hr + sorted(others, key=lambda e: seen[e], reverse=True)


def _guess_domain(entreprise: str, url: str) -> str | None:
    """Essaie de deviner le domaine d'une entreprise depuis son nom ou l'URL de l'annonce."""
    # 1. Depuis l'URL de l'annonce (ex: apply.migros.ch → migros.ch)
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        # Retire les sous-domaines jobup/jobs pour les annonces de jobboards
        if any(jb in host for jb in ["jobup", "jobs.ch", "indeed", "linkedin"]):
            host = ""
        if host and "." in host:
            parts = host.split(".")
            if len(parts) >= 2:
                return f"https://{'.'.join(parts[-2:])}"
    except Exception:
        pass

    # 2. Depuis le nom de l'entreprise → mot-clé connu
    name_lower = entreprise.lower()
    for keyword in KNOWN_EMAILS:
        if keyword in name_lower:
            return None  # on utilisera KNOWN_EMAILS directement

    # 3. Construire un domaine .ch depuis le nom (heuristique)
    clean = re.sub(r"[^a-z0-9]", "", name_lower)
    if len(clean) >= 3:
        return f"https://{clean}.ch"
    return None


def find_company_email_sync(
    entreprise: str,
    url: str,
    *,
    timeout: float = 8.0,
) -> str | None:
    """Version synchrone de find_company_email — pour usage dans le pipeline CLI."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, find_company_email(entreprise, url, timeout=timeout))
                return future.result()
        return loop.run_until_complete(find_company_email(entreprise, url, timeout=timeout))
    except Exception as exc:
        log.debug("email_finder.sync_error", error=str(exc))
        return None


async def find_company_email(
    entreprise: str,
    url: str,
    *,
    timeout: float = 8.0,
) -> str | None:
    """
    Cherche l'email de recrutement d'une entreprise.
    Retourne le meilleur email trouvé ou None.
    """
    if not entreprise:
        return None

    # 1. Emails connus en dur
    name_lower = entreprise.lower()
    for keyword, email in KNOWN_EMAILS.items():
        if keyword in name_lower:
            log.info("email_finder.known", entreprise=entreprise, email=email)
            return email

    # 2. Scraping du site entreprise
    domain = _guess_domain(entreprise, url)
    if not domain:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-CH,fr;q=0.9",
    }

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        verify=False,
    ) as client:
        # D'abord la page d'accueil
        pages_to_try = [domain] + [urljoin(domain, p) for p in CAREER_PATHS]

        for page_url in pages_to_try:
            try:
                resp = await client.get(page_url)
                if resp.status_code != 200:
                    continue
                emails = _extract_emails_from_html(resp.text)
                if emails:
                    best = emails[0]
                    log.info(
                        "email_finder.found",
                        entreprise=entreprise,
                        page=page_url,
                        email=best,
                    )
                    return best
            except Exception as exc:
                log.debug("email_finder.page_error", url=page_url, error=str(exc))
                continue

    log.info("email_finder.not_found", entreprise=entreprise, domain=domain)
    return None

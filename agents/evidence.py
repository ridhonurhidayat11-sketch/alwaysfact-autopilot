"""Evidence checker: the referee that does not trust the model.

A fact-checker (a model) hands in, per claim, an evidence URL and a VERBATIM quote from that page. This code
fetches the page itself and checks that the quote is really there and that every number in the claim appears
in the quote. A made-up URL, a made-up quote, or a changed number all fail. Plain code, no AI, no cost.
"""
import html
import re
import urllib.request

from factcheck import host, site

UA = "Mozilla/5.0 (compatible; science-page-checker/1.0)"
MAX_BYTES = 2_500_000
MIN_QUOTE = 25  # characters; a 3-word "quote" proves nothing


def fetch_text(url, timeout=25):
    """Visible text of a public https page. Raises on any error (callers treat that as 'unverifiable')."""
    if not host(url):
        raise ValueError("not an https URL")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        if not any(t in ctype for t in ("html", "text/plain", "xml")):
            raise ValueError(f"unsupported content type {ctype!r}")
        raw = r.read(MAX_BYTES).decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def norm(s):
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def quote_on_page(quote, page):
    q = norm(quote).strip(" .\"'")
    return len(q) >= MIN_QUOTE and q in norm(page)


def numbers(s):
    """Digit groups with separators removed, so '1,500' and '1.500' and '1500' compare equal."""
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", s or "")}


def verify_verdicts(post, verdicts, fetcher=fetch_text):
    """Returns (checked_verdicts, notes). A URL survives only if the page loads AND contains the quote.
    Unverified URLs are blanked, so the normal source rule (tier-1 or two independent sites) then fails the claim."""
    notes, cache, out = [], {}, []

    def verified(url, quote):
        if not url:
            return False
        if url not in cache:
            try:
                cache[url] = fetcher(url)
            except Exception as e:  # unreachable, paywalled, blocked, wrong type...
                cache[url] = None
                notes.append(f"{url}: could not be read ({type(e).__name__})")
        page = cache[url]
        if page is None:
            return False
        if not quote_on_page(quote, page):
            notes.append(f"{url}: the quote is not on the page")
            return False
        return True

    claims = post.get("claims", [])
    for v in verdicts:
        v = dict(v)
        n = v.get("claim")
        if isinstance(n, int) and 1 <= n <= len(claims) and v.get("verdict") == "supported":
            first = verified(v.get("evidence_url", ""), v.get("quote", ""))
            second = verified(v.get("second_evidence_url", ""), v.get("second_quote", ""))
            if not first:
                v["evidence_url"] = ""
            if not second:
                v["second_evidence_url"] = ""
            if first or second:
                quotes = (v.get("quote", "") if first else "") + " " + (v.get("second_quote", "") if second else "")
                missing = numbers(claims[n - 1]["text"]) - numbers(quotes)
                if missing:
                    notes.append(f"claim {n}: numbers {sorted(missing)} are not in the verified quote")
                    v["verdict"] = "unsupported"
                    v["note"] = f"numbers {sorted(missing)} not found in the quoted source text"
        out.append(v)
    return out, notes


__all__ = ["fetch_text", "quote_on_page", "numbers", "verify_verdicts", "site"]

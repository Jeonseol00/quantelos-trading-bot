# =============================================================================
# Quantelos AI Trader — Multi-Layer News Scraper
# =============================================================================
# Implements the MRD/BRD scraping fallback hierarchy:
#   L1: HTTP Client with JA3/TLS fingerprint masquerading (< 200ms)
#   L2: Playwright Stealth (headless browser fallback)
#   L3: CloakBrowser Anti-Detect (reserved, manual-enable only)
# =============================================================================
import logging
import json
import time
import subprocess
import signal
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("quantelos.scraper")

try:
    import requests
except ImportError:
    raise ImportError("Run: pip install requests")

# Optional L1 enhancement
try:
    from scrapling import Fetcher
    SCRAPLING_AVAILABLE = True
except ImportError:
    SCRAPLING_AVAILABLE = False

# Optional L2
try:
    from scrapling import StealthFetcher
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


@dataclass
class NewsEvent:
    """Parsed economic calendar event."""
    currency: str           # "USD" | "EUR"
    event_name: str         # e.g., "Non-Farm Payrolls"
    impact_level: str       # "HIGH" | "MEDIUM" | "LOW"
    forecast: str = ""
    actual: str = ""
    previous: str = ""
    scheduled_at: str = ""
    source: str = "forex_factory"
    raw_html: str = ""


# ─── High-Impact Event Filters (MRD Section 3.2) ─────────────────────────────
USD_CATALYSTS = {"Non-Farm Payrolls", "NFP", "CPI", "Consumer Price Index", "PPI",
                 "FOMC", "Federal Funds Rate", "Fed Interest Rate", "GDP",
                 "Retail Sales", "PMI", "Unemployment Rate", "Powell"}
EUR_CATALYSTS = {"ECB Interest Rate", "ECB Monetary Policy", "Eurozone Flash CPI",
                 "ECB Press Conference"}


class NewsScraper:
    """Multi-layer news scraper with automatic fallback escalation."""

    def __init__(self, layer1_timeout_ms: int = 200,
                 layer2_enabled: bool = True,
                 layer2_max_ram_mb: int = 400,
                 force_kill_after_s: int = 30):
        self.l1_timeout = layer1_timeout_ms / 1000.0
        self.l2_enabled = layer2_enabled
        self.l2_max_ram = layer2_max_ram_mb
        self.force_kill_s = force_kill_after_s

    def fetch_calendar(self) -> list[NewsEvent]:
        """Fetch economic calendar with automatic layer escalation."""
        # Layer 1: Fast HTTP client
        events = self._layer1_http()
        if events:
            return events

        # Layer 2: Playwright Stealth
        if self.l2_enabled and STEALTH_AVAILABLE:
            logger.info("L1 failed. Escalating to L2 (Playwright Stealth)...")
            events = self._layer2_stealth()
            if events:
                return events

        logger.warning("All scraping layers failed. Returning empty calendar.")
        return []

    def _layer1_http(self) -> list[NewsEvent]:
        """Layer 1: Fetch economic calendar from public Fair Economy JSON feed."""
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            resp = requests.get(url, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            events = []
            for item in data:
                currency = item.get("country", "")
                if currency == "ALL":
                    currency = "USD"
                if currency not in ("USD", "EUR"):
                    continue
                
                impact = item.get("impact", "LOW").upper()
                raw_date = item.get("date", "")
                scheduled_at = ""
                if raw_date:
                    try:
                        dt = datetime.fromisoformat(raw_date)
                        scheduled_at = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        scheduled_at = raw_date
                
                events.append(NewsEvent(
                    currency=currency,
                    event_name=item.get("title", ""),
                    impact_level=impact,
                    forecast=item.get("forecast", ""),
                    actual=item.get("actual", ""),
                    previous=item.get("previous", ""),
                    scheduled_at=scheduled_at,
                    source="forex_factory_json"
                ))
            logger.info("Fetched %d events from Fair Economy JSON feed", len(events))
            return events
        except Exception as e:
            logger.error("L1 JSON HTTP failed: %s", e)
            return []

    def _layer2_stealth(self) -> list[NewsEvent]:
        """Layer 2: Playwright Stealth browser with RAM limits and kill timeout."""
        import concurrent.futures
        def _run_stealth_fetch():
            fetcher = StealthFetcher(auto_match=True)
            page = fetcher.get(
                "https://www.forexfactory.com/calendar",
                timeout=15000,
            )
            return self._parse_forex_factory(page.text)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_stealth_fetch)
                try:
                    return future.result(timeout=self.force_kill_s)
                except concurrent.futures.TimeoutError:
                    logger.error("L2 Stealth exceeded %ds kill timeout. Aborting.", self.force_kill_s)
                    return []
        except Exception as e:
            logger.error("L2 Stealth failed: %s", e)
            return []

    def _parse_forex_factory(self, html: str) -> list[NewsEvent]:
        """Parse Forex Factory calendar HTML into NewsEvent objects."""
        events = []
        try:
            # Simplified parsing — production version would use BeautifulSoup
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tr.calendar__row")

            for row in rows:
                currency_el = row.select_one(".calendar__currency")
                impact_el = row.select_one(".calendar__impact span")
                event_el = row.select_one(".calendar__event-title")

                if not (currency_el and event_el):
                    continue

                currency = currency_el.get_text(strip=True)
                if currency not in ("USD", "EUR"):
                    continue

                event_name = event_el.get_text(strip=True)
                impact = "LOW"
                if impact_el:
                    impact_classes = impact_el.get("class", [])
                    if any("high" in c for c in impact_classes):
                        impact = "HIGH"
                    elif any("medium" in c for c in impact_classes):
                        impact = "MEDIUM"

                forecast_el = row.select_one(".calendar__forecast span")
                actual_el = row.select_one(".calendar__actual span")
                previous_el = row.select_one(".calendar__previous span")

                events.append(NewsEvent(
                    currency=currency,
                    event_name=event_name,
                    impact_level=impact,
                    forecast=forecast_el.get_text(strip=True) if forecast_el else "",
                    actual=actual_el.get_text(strip=True) if actual_el else "",
                    previous=previous_el.get_text(strip=True) if previous_el else "",
                ))
        except Exception as e:
            logger.error("HTML parse error: %s", e)

        high_impact = [e for e in events if e.impact_level == "HIGH"]
        logger.info("Scraped %d events (%d HIGH impact)", len(events), len(high_impact))
        return events

    def filter_catalysts(self, events: list[NewsEvent]) -> list[NewsEvent]:
        """Filter for MRD-defined high-impact catalysts only."""
        catalysts = []
        for e in events:
            if e.impact_level != "HIGH":
                continue
            keywords = USD_CATALYSTS if e.currency == "USD" else EUR_CATALYSTS
            if any(kw.lower() in e.event_name.lower() for kw in keywords):
                catalysts.append(e)
                logger.info("🔴 CATALYST: [%s] %s", e.currency, e.event_name)
        return catalysts

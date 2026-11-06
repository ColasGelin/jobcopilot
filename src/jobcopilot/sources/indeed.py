"""Indeed job search through JobSpy: public listings, no login. Scraping goes against Indeed's terms, so it is off unless
`indeed` is listed in `sources`; the realistic risk is a temporary IP block."""

from __future__ import annotations

from jobcopilot.sources.linkedin import LinkedInSource


class IndeedSource(LinkedInSource):
    name = "indeed"

    def __init__(self, searches, results, hours_old, country: str, **kw):
        super().__init__(searches, results, hours_old, **kw)
        self.country = country

    def options(self) -> dict:
        return {"country_indeed": self.country}

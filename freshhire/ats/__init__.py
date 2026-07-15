from freshhire.ats import greenhouse, lever

# provider name (as used in companies.yaml) -> fetch_jobs(slug) -> list[RawJob]
FETCHERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
}

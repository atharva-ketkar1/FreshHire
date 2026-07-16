from freshhire.ats import greenhouse, lever, workday

# provider name (as used in companies.yaml) -> fetch_jobs(slug) -> list[RawJob]
FETCHERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "workday": workday.fetch_jobs,
}

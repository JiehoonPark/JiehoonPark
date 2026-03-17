import requests
import os
import re
import time
from xml.etree import ElementTree as ET

USERNAME = "JiehoonPark"
TOKEN = os.environ.get("GH_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

GRAPHQL_URL = "https://api.github.com/graphql"
REST_URL = "https://api.github.com"


def graphql_query(query, variables=None):
    json_data = {"query": query}
    if variables:
        json_data["variables"] = variables
    resp = requests.post(
        GRAPHQL_URL,
        json=json_data,
        headers=HEADERS,
    )
    resp.raise_for_status()
    result = resp.json()
    if "errors" in result:
        print(f"GraphQL errors: {result['errors']}")
    return result


def get_stats():
    """Fetch repos, stars, commits, followers, contributed-to counts."""
    query = """
    query($login: String!) {
        user(login: $login) {
            repositories(ownerAffiliations: OWNER, privacy: PUBLIC) {
                totalCount
            }
            repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, ISSUE]) {
                totalCount
            }
            followers {
                totalCount
            }
            starredBy: repositories(ownerAffiliations: OWNER, first: 100) {
                nodes {
                    stargazerCount
                }
            }
            contributionsCollection {
                totalCommitContributions
                restrictedContributionsCount
            }
        }
    }
    """
    result = graphql_query(query, {"login": USERNAME})
    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    data = result.get("data", {}).get("user")
    if data is None:
        print(f"API response: {result}")
        print(f"Token present: {bool(TOKEN)}, Token length: {len(TOKEN)}")
        raise RuntimeError(
            f"Could not fetch user '{USERNAME}'. "
            "Check that GH_TOKEN is set and has the read:user scope."
        )

    repos = data["repositories"]["totalCount"]
    contributed = data["repositoriesContributedTo"]["totalCount"]
    followers = data["followers"]["totalCount"]
    stars = sum(node["stargazerCount"] for node in data["starredBy"]["nodes"])
    commits = (
        data["contributionsCollection"]["totalCommitContributions"]
        + data["contributionsCollection"]["restrictedContributionsCount"]
    )

    return repos, contributed, stars, commits, followers


def get_loc():
    """Fetch lines of code (additions/deletions) across all owned repos."""
    total_add = 0
    total_del = 0
    page = 1

    while True:
        resp = requests.get(
            f"{REST_URL}/users/{USERNAME}/repos?type=owner&per_page=100&page={page}",
            headers=HEADERS,
        )
        resp.raise_for_status()
        repos = resp.json()
        if not repos:
            break

        for repo in repos:
            if repo["fork"]:
                continue
            repo_name = repo["full_name"]
            contributors = None
            for attempt in range(3):
                stats_resp = requests.get(
                    f"{REST_URL}/repos/{repo_name}/stats/contributors",
                    headers=HEADERS,
                )
                if stats_resp.status_code == 200:
                    contributors = stats_resp.json()
                    break
                elif stats_resp.status_code == 202:
                    time.sleep(2)
                else:
                    break
            if not isinstance(contributors, list):
                continue
            for contributor in contributors:
                if contributor.get("author", {}).get("login") == USERNAME:
                    for week in contributor.get("weeks", []):
                        total_add += week.get("a", 0)
                        total_del += week.get("d", 0)
        page += 1

    total_loc = total_add - total_del
    return total_loc, total_add, total_del


def format_number(n):
    """Format number with commas: 1234 -> 1,234"""
    return f"{n:,}"


def format_compact(n):
    """Format large numbers compactly: 4810395 -> 4.8M, 1234 -> 1,234"""
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif abs_n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def calc_dots(line_template, values, target=60):
    """Calculate dot counts to maintain 60-char alignment.

    line_template: format string with {dot_id} and {val_id} placeholders
    values: dict of val_id -> value string
    Returns: dict of dot_id -> dot string
    """
    # Replace value placeholders with actual values to measure length
    filled = line_template
    for vid, val in values.items():
        filled = filled.replace(f"{{{vid}}}", val)
    # Count chars excluding dot placeholders
    import re as _re
    dot_ids = _re.findall(r'\{(.*?_dots)\}', line_template)
    for did in dot_ids:
        filled = filled.replace(f"{{{did}}}", "")
    chars_used = len(filled)
    remaining = target - chars_used
    # Distribute dots among placeholders
    n = len(dot_ids)
    if n == 0:
        return {}
    # Give proportional dots (first gets less, last gets more — like Andrew6rant)
    dots = {}
    if n == 1:
        dots[dot_ids[0]] = " " + "." * max(1, remaining - 2) + " "
    else:
        # Split: first section gets 1/3, second gets 2/3
        first = max(1, remaining // 3)
        second = max(1, remaining - first)
        dots[dot_ids[0]] = " " + "." * (first - 2) + " "
        dots[dot_ids[1]] = " " + "." * (second - 2) + " "
    return dots


def update_svg(filepath, stats):
    """Update SVG file with stats, recalculating dots for 60-char alignment."""
    repos, contributed, stars, commits, followers, loc, loc_add, loc_del = stats

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    repo_s = format_number(repos)
    contrib_s = format_number(contributed)
    star_s = format_number(stars)
    commit_s = format_number(commits)
    follower_s = format_number(followers)
    loc_s = format_number(loc)
    # Use compact format for add/del if the line would be too long
    loc_add_s = format_number(loc_add)
    loc_del_s = format_number(loc_del)
    line3_test = len(f". Lines of Code on GitHub:{loc_s} ( {loc_add_s}++, {loc_del_s}-- )")
    if line3_test > 58:
        loc_add_s = format_compact(loc_add)
        loc_del_s = format_compact(loc_del)
    line3_test = len(f". Lines of Code on GitHub:{loc_s} ( {loc_add_s}++, {loc_del_s}-- )")
    if line3_test > 58:
        loc_s = format_compact(loc)

    # Line 1: ". Repos: DOTS VAL {Contributed: VAL} | Stars: DOTS VAL" = 60
    # Fixed parts (without dots sections): ". Repos:" + val + " {Contributed: " + val + "} | Stars:" + val
    line1_base = len(f". Repos:{repo_s} {{Contributed: {contrib_s}}} | Stars:{star_s}")
    line1_dots_total = max(2, 60 - line1_base - 4)  # subtract 4 for spaces around 2 dot groups
    repo_dots_n = max(1, line1_dots_total // 3)
    star_dots_n = max(1, line1_dots_total - repo_dots_n)
    repo_dots = " " + "." * repo_dots_n + " "
    star_dots = " " + "." * star_dots_n + " "

    # Line 2: ". Commits: DOTS VAL | Followers: DOTS VAL" = 60
    line2_base = len(f". Commits:{commit_s} | Followers:{follower_s}")
    line2_dots_total = max(2, 60 - line2_base - 4)
    commit_dots_n = max(1, int(line2_dots_total * 0.7))
    follower_dots_n = max(1, line2_dots_total - commit_dots_n)
    commit_dots = " " + "." * commit_dots_n + " "
    follower_dots = " " + "." * follower_dots_n + " "

    # Line 3: ". Lines of Code on GitHub:DOTS VAL ( VAL++,  VAL-- )" = 60
    line3_base = len(f". Lines of Code on GitHub:{loc_s} ( {loc_add_s}++, {loc_del_s}-- )")
    line3_remaining = 60 - line3_base
    if line3_remaining >= 3:
        # Enough room: dots + space before value, extra space before loc_del
        loc_dots = "." * (line3_remaining - 2) + " "
        loc_del_space = " "
    elif line3_remaining == 2:
        loc_dots = " "
        loc_del_space = " "
    elif line3_remaining == 1:
        loc_dots = " "
        loc_del_space = ""
    else:
        # No room at all: remove dots and extra space
        loc_dots = ""
        loc_del_space = ""

    # Apply all replacements: values + dots
    replacements = {
        "repo_data": repo_s,
        "contrib_data": contrib_s,
        "star_data": star_s,
        "commit_data": commit_s,
        "follower_data": follower_s,
        "loc_data": loc_s,
        "loc_add": loc_add_s,
        "loc_del": loc_del_s,
        "repo_data_dots": repo_dots,
        "star_data_dots": star_dots,
        "commit_data_dots": commit_dots,
        "follower_data_dots": follower_dots,
        "loc_data_dots": loc_dots,
        "loc_del_dots": loc_del_space,
    }

    for element_id, value in replacements.items():
        pattern = rf'(id="{element_id}">)[^<]*(</tspan>|<)'
        replacement = rf"\g<1>{value}\g<2>"
        content = re.sub(pattern, replacement, content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Updated {filepath}")


def main():
    print(f"Fetching stats for {USERNAME}...")

    repos, contributed, stars, commits, followers = get_stats()
    print(f"Repos: {repos}, Contributed: {contributed}, Stars: {stars}, Commits: {commits}, Followers: {followers}")

    loc, loc_add, loc_del = get_loc()
    print(f"LOC: {loc}, Additions: {loc_add}, Deletions: {loc_del}")

    stats = (repos, contributed, stars, commits, followers, loc, loc_add, loc_del)

    update_svg("dark_mode.svg", stats)
    update_svg("light_mode.svg", stats)

    print("Done!")


if __name__ == "__main__":
    main()

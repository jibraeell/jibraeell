
import datetime
import hashlib
import os
import time
from pathlib import Path

import requests
from dateutil import relativedelta
from lxml import etree

 
# GitHub Actions environment variables 
USER_NAME = os.environ.get("USER_NAME", "").strip()
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()

if not USER_NAME:
    raise RuntimeError("Missing USER_NAME environment variable.")
if not ACCESS_TOKEN:
    raise RuntimeError("Missing ACCESS_TOKEN repository secret.")

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE = CACHE_DIR / (
    hashlib.sha256(USER_NAME.encode("utf-8")).hexdigest() + ".txt"
)

QUERY_COUNT = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "recursive_loc": 0,
    "graph_commits": 0,
    "loc_query": 0,
    "rest_owned_repo_stars": 0,
}

OWNER_ID = None
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def query_count(name):
    QUERY_COUNT[name] = QUERY_COUNT.get(name, 0) + 1


def simple_request(func_name, query, variables):
    """Execute a GitHub GraphQL request and report errors clearly."""
    try:
        response = SESSION.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"{func_name} request failed: {exc}") from exc

    if payload.get("errors"):
        messages = "; ".join(
            error.get("message", str(error))
            for error in payload["errors"]
        )
        raise RuntimeError(f"{func_name} GraphQL error: {messages}")

    if not payload.get("data"):
        raise RuntimeError(
            f"{func_name} returned no data. Check the username and token permissions."
        )

    return payload["data"]


def format_plural(unit):
    return "" if unit == 1 else "s"


def daily_readme(birthday):
    """Return elapsed time since the configured birthday."""
    now = datetime.datetime.now()
    diff = relativedelta.relativedelta(now, birthday)
    birthday_today = diff.months == 0 and diff.days == 0

    return (
        f"{diff.years} year{format_plural(diff.years)}, "
        f"{diff.months} month{format_plural(diff.months)}, "
        f"{diff.days} day{format_plural(diff.days)}"
        f"{' 🎂' if birthday_today else ''}"
    )


def user_getter(username):
    """Return GitHub user ID and account creation date."""
    query_count("user_getter")

    query = """
    query($login: String!) {
        user(login: $login) {
            id
            createdAt
        }
    }
    """

    data = simple_request("user_getter", query, {"login": username})
    user = data.get("user")

    if not user:
        raise RuntimeError(f"GitHub user '{username}' was not found.")

    return {"id": user["id"]}, user["createdAt"]


def follower_getter(username):
    """Return the user's current follower count."""
    query_count("follower_getter")

    query = """
    query($login: String!) {
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }
    """

    data = simple_request("follower_getter", query, {"login": username})
    user = data.get("user") or {}
    followers = user.get("followers") or {}

    return followers.get("totalCount") or 0


def graph_commits(start_date, end_date):
    """Return contribution count for a date range."""
    query_count("graph_commits")

    query = """
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }
    """

    data = simple_request(
        "graph_commits",
        query,
        {
            "start_date": start_date,
            "end_date": end_date,
            "login": USER_NAME,
        },
    )

    user = data.get("user") or {}
    collection = user.get("contributionsCollection") or {}
    calendar = collection.get("contributionCalendar") or {}

    return calendar.get("totalContributions") or 0


def rest_owned_repo_stars():
    """Count stars received by repositories owned by the authenticated user."""
    query_count("rest_owned_repo_stars")
    total_stars = 0
    page = 1

    while True:
        try:
            response = SESSION.get(
                "https://api.github.com/user/repos",
                params={
                    "affiliation": "owner",
                    "visibility": "all",
                    "per_page": 100,
                    "page": page,
                },
                timeout=45,
            )
            response.raise_for_status()
            repos = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                f"Could not retrieve owned repositories for star count: {exc}"
            ) from exc

        if not isinstance(repos, list):
            raise RuntimeError("GitHub returned an unexpected repository response.")

        if not repos:
            break

        total_stars += sum(
            repo.get("stargazers_count") or 0
            for repo in repos
        )

        if len(repos) < 100:
            break

        page += 1

    return total_stars


def graph_repos_stars(count_type, owner_affiliation):
    """Count stars via REST and repository totals via GraphQL."""
    if count_type == "stars":
        return rest_owned_repo_stars()

    if count_type != "repos":
        raise ValueError(f"Unsupported count type: {count_type}")

    query_count("graph_repos_stars")

    query = """
    query(
        $owner_affiliation: [RepositoryAffiliation],
        $login: String!
    ) {
        user(login: $login) {
            repositories(
                first: 1,
                ownerAffiliations: $owner_affiliation
            ) {
                totalCount
            }
        }
    }
    """

    data = simple_request(
        "graph_repos_stars",
        query,
        {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
        },
    )

    user = data.get("user") or {}
    repositories = user.get("repositories") or {}

    return repositories.get("totalCount") or 0


def loc_query(owner_affiliation, comment_size=7, force_cache=False):
    """Collect repositories and update cached lines-of-code statistics."""
    query_count("loc_query")
    edges = []
    cursor = None

    while True:
        query = """
        query(
            $owner_affiliation: [RepositoryAffiliation],
            $login: String!,
            $cursor: String
        ) {
            user(login: $login) {
                repositories(
                    first: 60,
                    after: $cursor,
                    ownerAffiliations: $owner_affiliation
                ) {
                    edges {
                        node {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                    pageInfo {
                        endCursor
                        hasNextPage
                    }
                }
            }
        }
        """

        data = simple_request(
            "loc_query",
            query,
            {
                "owner_affiliation": owner_affiliation,
                "login": USER_NAME,
                "cursor": cursor,
            },
        )

        user = data.get("user") or {}
        repositories = user.get("repositories") or {}

        edges.extend(repositories.get("edges") or [])
        page_info = repositories.get("pageInfo") or {}

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("Repository pagination returned no cursor.")

    return cache_builder(edges, comment_size, force_cache)


def recursive_loc(
    owner,
    repo_name,
    addition_total=0,
    deletion_total=0,
    my_commits=0,
    cursor=None,
):
    """Count commits authored by this user across paginated history."""
    query_count("recursive_loc")

    query = """
    query($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            edges {
                                node {
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    additions
                                    deletions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }
    """

    data = simple_request(
        "recursive_loc",
        query,
        {
            "repo_name": repo_name,
            "owner": owner,
            "cursor": cursor,
        },
    )

    repository = data.get("repository") or {}
    branch = repository.get("defaultBranchRef")

    if not branch:
        return addition_total, deletion_total, my_commits

    target = branch.get("target") or {}
    history = target.get("history") or {}
    edges = history.get("edges") or []

    for edge in edges:
        commit = (edge or {}).get("node") or {}
        author = commit.get("author") or {}
        user = author.get("user") or {}

        if OWNER_ID and user.get("id") == OWNER_ID.get("id"):
            my_commits += 1
            addition_total += commit.get("additions") or 0
            deletion_total += commit.get("deletions") or 0

    page_info = history.get("pageInfo") or {}

    if page_info.get("hasNextPage"):
        next_cursor = page_info.get("endCursor")
        if not next_cursor:
            raise RuntimeError("Commit pagination returned no cursor.")

        return recursive_loc(
            owner,
            repo_name,
            addition_total,
            deletion_total,
            my_commits,
            next_cursor,
        )

    return addition_total, deletion_total, my_commits


def cache_builder(
    edges,
    comment_size,
    force_cache,
    loc_add=0,
    loc_del=0,
):
    """Update repository cache and return total additions and deletions."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    valid_edges = []
    for edge in edges or []:
        repository = (edge or {}).get("node") or {}
        if repository.get("nameWithOwner"):
            valid_edges.append(edge)

    comments = []
    cached_rows = {}

    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as file:
            existing = file.readlines()

        comments = existing[:comment_size]

        for line in existing[comment_size:]:
            parts = line.split()
            if len(parts) >= 5:
                cached_rows[parts[0]] = parts

    while len(comments) < comment_size:
        comments.append(
            "This line is a comment block. Write whatever you want here.\n"
        )

    updated_rows = []
    all_cached = True

    for edge in valid_edges:
        repository = edge["node"]
        full_name = repository["nameWithOwner"]
        repo_hash = hashlib.sha256(full_name.encode("utf-8")).hexdigest()

        branch = repository.get("defaultBranchRef") or {}
        target = branch.get("target") or {}
        history = target.get("history") or {}
        commit_count = history.get("totalCount") or 0

        old = cached_rows.get(repo_hash)

        if (
            not force_cache
            and old
            and len(old) >= 5
            and old[1].isdigit()
            and int(old[1]) == commit_count
        ):
            row = old[:5]
        elif commit_count == 0:
            row = [repo_hash, "0", "0", "0", "0"]
            all_cached = False
        else:
            owner, repo_name = full_name.split("/", 1)
            additions, deletions, commits = recursive_loc(owner, repo_name)

            row = [
                repo_hash,
                str(commit_count),
                str(commits),
                str(additions),
                str(deletions),
            ]
            all_cached = False

        updated_rows.append(" ".join(row) + "\n")

    with CACHE_FILE.open("w", encoding="utf-8") as file:
        file.writelines(comments)
        file.writelines(updated_rows)

    for line in updated_rows:
        parts = line.split()
        loc_add += int(parts[3])
        loc_del += int(parts[4])

    return [loc_add, loc_del, loc_add - loc_del, all_cached]


def commit_counter(comment_size=7):
    """Count commits attributed to the user in the repository cache."""
    if not CACHE_FILE.exists():
        raise RuntimeError("Cache file is missing; run loc_query first.")

    total = 0

    with CACHE_FILE.open("r", encoding="utf-8") as file:
        lines = file.readlines()[comment_size:]

    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            try:
                total += int(parts[2])
            except ValueError:
                continue

    return total


def find_and_replace(root, element_id, new_text):
    """Replace the text of an SVG element by its ID."""
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = str(new_text)


def justify_format(root, element_id, new_text, length=0):
    """Format a value and adjust its adjacent dot leader."""
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"

    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)

    just_len = max(0, length - len(new_text))

    if just_len <= 2:
        dot_string = {0: "", 1: " ", 2: ". "}[just_len]
    else:
        dot_string = " " + ("." * just_len) + " "

    find_and_replace(root, f"{element_id}_dots", dot_string)


def svg_overwrite(
    filename,
    age_data,
    commit_data,
    star_data,
    repo_data,
    contrib_data,
    follower_data,
    loc_data,
):
    """Update statistic text in an existing SVG."""
    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"Required SVG file '{filename}' was not found."
        )

    tree = etree.parse(filename)
    root = tree.getroot()

    justify_format(root, 'commit_data', commit_data, 22)
    justify_format(root, 'star_data', star_data, 14)
    justify_format(root, 'repo_data', repo_data, 8)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    justify_format(root, 'loc_data', loc_data[2], 7)
    justify_format(root, 'loc_add', loc_data[0])
    justify_format(root, 'loc_del', loc_data[1], 7)
    tree.write(filename, encoding="utf-8", xml_declaration=True)


def perf_counter(function, *args):
    """Return a function result and its elapsed time."""
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


def formatter(name, elapsed, result=None):
    """Print elapsed time and return the optional result."""
    units = (
        f"{elapsed:.4f} s"
        if elapsed > 1
        else f"{elapsed * 1000:.4f} ms"
    )
    print(f"   {name + ':':<23}{units:>12}")
    return result


def main():
    global OWNER_ID

    print("Calculation times:")

    user_data, elapsed = perf_counter(user_getter, USER_NAME)
    OWNER_ID, account_created = user_data
    formatter("account data", elapsed)

    # Preserve your configured birthday.
    birthday = datetime.datetime(2005, 6, 28)
    age_data, elapsed = perf_counter(daily_readme, birthday)
    formatter("age calculation", elapsed)

    loc_data, elapsed = perf_counter(
        loc_query,
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
        7,
        False,
    )
    formatter("LOC calculation", elapsed)

    commit_data, elapsed = perf_counter(commit_counter, 7)
    formatter("commit count", elapsed)

    star_data, elapsed = perf_counter(
        graph_repos_stars, "stars", ["OWNER"]
    )
    formatter("star count", elapsed)

    repo_data, elapsed = perf_counter(
        graph_repos_stars, "repos", ["OWNER"]
    )
    formatter("owned repositories", elapsed)

    contrib_data, elapsed = perf_counter(
        graph_repos_stars,
        "repos",
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
    )
    formatter("contributed repositories", elapsed)

    follower_data, elapsed = perf_counter(follower_getter, USER_NAME)
    formatter("follower count", elapsed)

    formatted_loc = [f"{value:,}" for value in loc_data[:3]]

    svg_overwrite(
        "dark_mode.svg",
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        formatted_loc,
    )

    svg_overwrite(
        "light_mode.svg",
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        formatted_loc,
    )

    print("Total GitHub API calls:", sum(QUERY_COUNT.values()))
    for name, count in QUERY_COUNT.items():
        print(f"   {name + ':':<28}{count:>6}")


if __name__ == "__main__":
    main()

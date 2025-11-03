import os
import requests
from datetime import datetime, timedelta, timezone
from dateutil.parser import parse
from collections import defaultdict
import re

# ================================
# === CONFIGURATION SECTION ===
# ================================

# GitHub Personal Access Token (used for authentication)
# 👉 You should ideally store this in an environment variable for security.
GITHUB_TOKEN =  ''

# Repository information
REPO = ''  # Target repo (format: owner/repo)
OWNER = ''                   # GitHub username
PROJECT_NUMBER =                       # ProjectV2 board number (for GraphQL API)

# Common headers for GitHub API requests
headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

# Current time (UTC)
now = datetime.now(timezone.utc)

# Define time range → get only the last 7 days of data
since = (now - timedelta(days=7)).isoformat()

# Global statistics counters
stats = {
    "add": 0,
    "fix": 0,
    "refactor": 0,
    "other_commits": 0,
    "issues_opened": 0,
    "issues_closed": 0,
    "bugs_closed": 0,
    "tasks_completed": 0
}

# Dictionary to store per-contributor performance data
contributors = defaultdict(lambda: {
    'kpi': 0,
    'bug_fixes': 0,
    'total_actions': 0,
    'add_commits': 0,
    'refactor_commits': 0,
    'active_days': set()  # keeps track of unique active days per contributor
})

# List to hold project tasks updated this week
project_tasks = []

# ==================================================
# === 1. FETCH COMMITS AND ANALYZE CONTRIBUTIONS ===
# ==================================================
def get_commits():
    """Fetch commits from the last 7 days and classify them by type (add, fix, refactor, etc.)"""
    url = f'https://api.github.com/repos/{REPO}/commits'
    params = {'since': since}
    response = requests.get(url, headers=headers, params=params)
    commits = response.json()

    # If response is not a list, API returned an error
    if not isinstance(commits, list):
        print("GitHub API Error in Commits:", commits)
        return

    # Iterate through all commits in the fetched data
    for commit in commits:
        msg = commit['commit']['message'].lower()  # Commit message
        author = commit['commit']['author']['name'] if commit['commit']['author'] else 'Unknown'
        date = parse(commit['commit']['author']['date']).date()  # Commit date

        # Classify commits by keywords in message
        if 'fix' in msg:
            stats['fix'] += 1
            contributors[author]['kpi'] += 2.5
            contributors[author]['bug_fixes'] += 1
        elif 'add' in msg or 'enhancement' in msg:
            stats['add'] += 1
            contributors[author]['kpi'] += 2
            contributors[author]['add_commits'] += 1
        elif 'refactor' in msg:
            stats['refactor'] += 1
            contributors[author]['kpi'] += 1
            contributors[author]['refactor_commits'] += 1
        else:
            stats['other_commits'] += 1
            contributors[author]['kpi'] += 1

        # Count total actions and mark active days
        contributors[author]['total_actions'] += 1
        contributors[author]['active_days'].add(date)

# ===================================
# === 2. FETCH ISSUES AND STATS ===
# ===================================
def get_issues():
    """Fetch issues opened/closed in the last 7 days and calculate stats"""
    url = f'https://api.github.com/repos/{REPO}/issues'
    params = {'state': 'all', 'since': since}
    response = requests.get(url, headers=headers, params=params)
    issues = response.json()

    for issue in issues:
        created = parse(issue['created_at']).astimezone(timezone.utc)
        closed_at = parse(issue['closed_at']).astimezone(timezone.utc) if issue.get('closed_at') else None
        # Check if the issue is a bug (has "bug" label)
        is_bug = any(label['name'].lower() == 'bug' for label in issue.get('labels', []))

        # Count new issues created this week
        if created >= now - timedelta(days=7):
            stats['issues_opened'] += 1

        # Count issues closed this week (and if they were bugs)
        if closed_at and closed_at >= now - timedelta(days=7):
            stats['issues_closed'] += 1
            if is_bug:
                stats['bugs_closed'] += 1
            author = issue['user']['login']
            contributors[author]['kpi'] += 2
            contributors[author]['total_actions'] += 1

# ===========================================
# === 3. FETCH PROJECTV2 TASKS (GraphQL) ===
# ===========================================
def get_project_tasks():
    """Fetch project board tasks updated this week using GitHub GraphQL API"""
    graphql_url = "https://api.github.com/graphql"

    # GraphQL query to fetch issues/pull requests/draft issues from the project
    graphql_query = f"""
    {{
      user(login: \"{OWNER}\") {{
        projectV2(number: {PROJECT_NUMBER}) {{
          items(first: 50) {{
            nodes {{
              content {{
                __typename
                ... on Issue {{ title updatedAt }}
                ... on PullRequest {{ title updatedAt }}
                ... on DraftIssue {{ title updatedAt }}
              }}
              updatedAt
              fieldValues(first: 10) {{
                nodes {{
                  ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
                  ... on ProjectV2ItemFieldTextValue {{ text }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """

    # Execute GraphQL query
    response = requests.post(graphql_url, json={'query': graphql_query}, headers=headers)
    data = response.json()

    try:
        items = data['data']['user']['projectV2']['items']['nodes']

        # Loop through project tasks
        for item in items:
            updated = parse(item['updatedAt']).astimezone(timezone.utc)
            title = item['content'].get('title', 'Untitled') if item.get('content') else 'Untitled'

            # Collect task statuses from the project fields
            status_list = []
            for field in item['fieldValues']['nodes']:
                name = field.get('name')
                text = field.get('text')
                if name:
                    status_list.append(name.lower())
                elif text:
                    status_list.append(text.lower())

            # Count tasks completed within the week
            if updated >= now - timedelta(days=7):
                if any(status in ['done', 'completed'] for status in status_list):
                    stats['tasks_completed'] += 1

                project_tasks.append({
                    "title": title,
                    "status": status_list[0] if status_list else "unknown",
                    "updated": updated.strftime('%Y-%m-%d %H:%M')
                })
    except Exception as e:
        print("❌ Error fetching project tasks:", e)

# =======================================
# === 4. GLOBAL KPI CALCULATION ===
# =======================================
def calculate_kpi():
    """Compute the global KPI score based on commits, bug fixes, and tasks completed"""
    return (
        (2 * stats['add']) +
        (2.5 * stats['fix']) +
        (2 * stats['bugs_closed']) +
        (3 * stats['tasks_completed'])
    )

# ================================================
# === 5. GET PREVIOUS WEEK KPI FROM REPORTS ===
# ================================================
def get_previous_week_kpi():
    """Retrieve the KPI value from the previous week's report"""
    reports_dir = 'reports'
    if not os.path.exists(reports_dir):
        return 0

    report_files = sorted(
        [f for f in os.listdir(reports_dir) if f.startswith("Weekly_Report_Week_")],
        reverse=True
    )

    current_week = now.isocalendar()[1]
    for fname in report_files:
        week_num = int(re.findall(r'\d+', fname)[-1])
        if week_num < current_week:
            with open(os.path.join(reports_dir, fname), 'r', encoding='utf-8') as f:
                for line in f:
                    if "This Week KPI:" in line:
                        match = re.search(r'\*\*(\d+(?:\.\d+)?)\*\*', line)
                        if match:
                            return float(match.group(1))
    return 0

# ============================================
# === 6. ASSIGN CONTRIBUTOR BADGES ===
# ============================================
def assign_badges():
    """Assign badges to contributors based on their performance and activity"""
    badges = defaultdict(list)
    if not contributors:
        return badges

    # Identify top contributor by KPI
    top = max(contributors.items(), key=lambda x: x[1]['kpi'], default=None)
    if top:
        badges[top[0]].append("Top Contributor")

    # Badge for most bug fixes
    bug = max(contributors.items(), key=lambda x: x[1]['bug_fixes'], default=None)
    if bug and bug[1]['bug_fixes'] > 0:
        badges[bug[0]].append("Bug Squasher")

    # Most active contributor (total actions)
    active = max(contributors.items(), key=lambda x: x[1]['total_actions'], default=None)
    if active:
        badges[active[0]].append("Most Active")

    # Most feature additions
    feature = max(contributors.items(), key=lambda x: x[1]['add_commits'], default=None)
    if feature and feature[1]['add_commits'] > 0:
        badges[feature[0]].append("Feature Creator")

    # Most code refactors
    refactor = max(contributors.items(), key=lambda x: x[1]['refactor_commits'], default=None)
    if refactor and refactor[1]['refactor_commits'] > 0:
        badges[refactor[0]].append("Code Refactorer")

    # Consistent contributor (active ≥ 3 days in a week)
    for contributor, data in contributors.items():
        if len(data['active_days']) >= 3:
            badges[contributor].append("Consistent Contributor")

    return badges

# ===============================================
# === 7. GENERATE MARKDOWN REPORT OUTPUT ===
# ===============================================
def generate_markdown_report(kpi_score, previous_kpi, badges):
    """Generate the final weekly Markdown report with all statistics and insights"""
    week_number = now.isocalendar()[1]
    os.makedirs('reports', exist_ok=True)
    filename = f'reports/Weekly_Report_Week_{week_number}.md'

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"# Weekly Report - Week {week_number}\n")
        f.write(f"**Date Range:** {since} → {now.isoformat()}\n\n")

        # Technical summary
        f.write("## Technical Achievements\n")
        f.write(f"- Add commits: {stats['add']}\n")
        f.write(f"- Fix commits: {stats['fix']}\n")
        f.write(f"- Refactor commits: {stats['refactor']}\n")
        f.write(f"- Other commits: {stats['other_commits']}\n\n")

        # Bugs resolved
        f.write("## Bugs Resolved\n")
        f.write(f"- Issues Closed: {stats['issues_closed']}\n")
        f.write(f"- Bug-type Issues Closed: {stats['bugs_closed']}\n\n")

        # Completed project tasks
        f.write("## Project Tasks Completed\n")
        f.write(f"- Tasks marked Done/Completed: {stats['tasks_completed']}\n\n")

        # KPI comparison section
        f.write("## KPI Score Summary\n")
        f.write(f"- This Week KPI: **{kpi_score}**\n")
        f.write(f"- Previous Week KPI: **{previous_kpi}**\n")
        trend = "⬆ Increase" if kpi_score > previous_kpi else "⬇ Decrease" if kpi_score < previous_kpi else "Stable"
        f.write(f"- Trend: {trend}\n\n")

        # Contributor details and badges
        f.write("## Contributor Performance\n")
        for contributor, data in sorted(contributors.items(), key=lambda x: x[1]['kpi'], reverse=True):
            f.write(f"### 🔹 {contributor}\n")
            f.write(f"- KPI Score: **{data['kpi']:.2f}**\n")
            f.write(f"- Total Actions: {data['total_actions']}\n")
            f.write(f"- Bug Fixes: {data['bug_fixes']}, Add Commits: {data['add_commits']}, Refactor Commits: {data['refactor_commits']}\n")
            f.write(f"- Active Days: {len(data['active_days'])}\n")
            badge_list = badges.get(contributor, [])
            if badge_list:
                f.write(f"- Badges: {', '.join(badge_list)}\n")
            f.write("\n")

        # List of updated project tasks this week
        f.write("## Tasks Updated This Week\n")
        for task in project_tasks:
            f.write(f"- **{task['title']}** — Status: *{task['status']}*, Updated: {task['updated']}\n")

    print(f"✅ Report generated: {filename}")

# ===============================================
# === MAIN EXECUTION FLOW ===
# ===============================================
if __name__ == "__main__":
    # Step 1: Collect commits and classify them
    get_commits()

    # Step 2: Fetch issues and bug-related activity
    get_issues()

    # Step 3: Retrieve project board task updates
    get_project_tasks()

    # Step 4: Calculate this week's KPI and compare to last week's
    current_kpi = calculate_kpi()
    previous_kpi = get_previous_week_kpi()

    # Step 5: Assign performance badges
    badges = assign_badges()

    # Step 6: Generate and save Markdown report
    generate_markdown_report(current_kpi, previous_kpi, badges)

---
name: fetch_flutter_discussions
description: Fetch Flutter community discussions, use agent judgment to filter items relevant to the query, summarize key topics in bullet points, and generate a markdown file listing sources by topic.
---

# fetch_flutter_discussions

Fetch Flutter discussions across public platforms (GitHub, Stack Overflow, and Reddit), evaluate and filter items based on the user query using assistant judgment, categorize them into topics, generate a markdown file listing all sources, and output a bulleted summary.

## Parameters

- **source** (string, optional): Target platform (`all`, `github`, `stackoverflow`, `reddit`, `twitter`). Defaults to `all`.
- **query** (string, optional): Topic, question, or focus area used by the agent to filter fetched items (for example, `accessibility`, `go_router`, `impeller`, `state management`).
- **days** (integer, optional): Number of past days to look back. Defaults to `180` (6 months).
- **output_markdown** (string, optional): Filename for the topic-grouped markdown sources file. Defaults to `discussions_by_topic.md`.
- **output_json** (string, optional): Filename for the raw fetched items before agent filtering. Defaults to `raw_discussions.json`.

Both outputs are regenerable dumps, so they are written to the repo's
gitignored `tmp/fetch_flutter_discussions/` directory. A bare filename
resolves there rather than in the current directory. Pass an absolute path, or
`--output-dir`, only when you deliberately want them somewhere else.

## Instructions

1. Run the discussion fetcher script at [fetch_discussions.py](file://~/git/skills/skills/fetch_flutter_discussions/scripts/fetch_discussions.py) to collect Flutter discussions for the specified timeframe:
   ```bash
   python3 ~/git/skills/skills/fetch_flutter_discussions/scripts/fetch_discussions.py \
     --source all --days 180 --max-items 50 --output-json raw_discussions.json
   ```
   The script prints the absolute path it wrote to. Read that path in the next
   step rather than assuming a location.
2. Read the collected items from the generated JSON file.
3. Use your own judgment to filter the items based on the user's query or focus area:
   - Include items that directly address or relate to the query context, even if they use different terminology or synonyms.
   - Discard unrelated issues, noise, spam, and duplicate questions.
4. Categorize the filtered items into coherent topic groups (such as Performance and Engine, Framework and Widgets, Tooling and Build, Routing and Navigation, Accessibility).
5. Write the filtered items into the target markdown file (`output_markdown`), grouping them by topic with clickable URLs, authors, dates, and concise descriptions.
6. Provide a concise bullet point summary of the key findings, recurring themes, and user discussions in your final response, referencing the created markdown file.

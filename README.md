# Hi, I'm jibraeell 👋

This repository automatically refreshes my GitHub profile statistics and displays a custom ASCII portrait generated from my photo.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jibraeell/jibraeell/main/dark_mode.svg">
  <img alt="jibraeell's GitHub profile statistics and ASCII portrait" src="https://raw.githubusercontent.com/jibraeell/jibraeell/main/light_mode.svg">
</picture>

## Setup

1. Create a **public repository named exactly `jibraeell`** on GitHub (if you don't already have one).
2. Upload the contents of this project to the repository's `main` branch.
3. In **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `ACCESS_TOKEN`: a GitHub personal access token with the minimum permissions needed to read the profile/repository statistics. The original script uses GitHub GraphQL and may require a fine-grained token.
4. In **Settings → Actions → General**, allow GitHub Actions to read and write repository contents.
5. Run **Actions → README build → Run workflow** once. The workflow also runs daily at 04:00 UTC and on pushes to `main`.

Never commit a personal access token into a file or share it in chat.

## Customize
- The ASCII portrait is embedded in `dark_mode.svg` and `light_mode.svg`.
- Edit the profile details in both SVG files to add your own OS, languages, links, and hobbies.
- Stats are refreshed by `today.py`. The workflow uses `jibraeell` as the GitHub username.

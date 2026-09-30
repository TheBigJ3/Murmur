# Changelog

All notable changes to Murmur are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Add notes under `[Unreleased]`;
`scripts/release.sh` turns them into a versioned section.

## [Unreleased]

- Initial project skeleton: skill template, install and release scripts, runner and schema placeholders.
- API mapping prompt: finds the API and existing test credential rules, writes `.murmur/loadgraph.json` and `.murmur/skips.md`, and implements key protected, dev only skip endpoints.

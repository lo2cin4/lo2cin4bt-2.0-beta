# README Acceptance Criteria

Use this checklist before promoting README changes or a GitHub demo snapshot.

## Required Structure

- `README.md` is the default Traditional Chinese README.
- `README.en.md` is the English README.
- Both start with the reviewed hero image followed by a concise lo2cin4bt positioning line.
- Both use lowercase `lo2cin4bt` for the product brand in visible README copy.
- Both explain that lo2cin4bt is a local research/backtesting platform, not financial advice or live trading software.
- Both explain "what is lo2cin4bt" and "why choose lo2cin4bt" before install
  or screenshot walkthrough sections.
- Both include a BTCUSDT daily dual-moving-average beginner example.
- Both explain that beginners mainly work inside local `workspace/` files while
  source/docs/tests stay in the repo.
- Both include an AI-assisted install path where the user can ask AI to perform
  setup and local launch.
- The beginner documentation section should stay short. Required user-facing
  links are install, tutorial, and troubleshooting. Changelog, roadmap, release
  notes, README acceptance criteria, and raw skill contract links are optional
  maintainer references and should not be pushed into the beginner flow unless
  explicitly requested.

## Language Requirements

- Chinese README uses Traditional Chinese teaching and prompt copy.
- English README uses English teaching and prompt copy.
- Code paths, commands, URLs, schema keys, product names, and asset symbols may remain literal.
- Chinese README must not use stale English UI section names when a Chinese label exists.
- README copy should avoid project-building language such as "we are
  building this into..." and avoid unnecessary beginner-facing engineering
  jargon when plain wording is enough.

## Visual Requirements

- Chinese README references reviewed static screenshots under `assets/readme/zh-Hant/`.
- English README references reviewed static screenshots under `assets/readme/en/`.
- Each language uses the reviewed static screenshot pair:
  `01-overview.webp` and `02-run-center.webp`.
- Each language links one YouTube walkthrough video:
  Chinese `https://youtu.be/XIPYRn3H0tU?si=5RoLzrmGLEG6uxaD`,
  English `https://youtu.be/03CduKFc4sg?si=GE7Y2EFKnsiF3HFV`.
- Screenshots and videos are UI walkthrough evidence only.
- The shared hero image `assets/readme/hero/lo2cin4btneon.jpg` is allowed
  before the README positioning copy when intentionally referenced by both READMEs.
- WFA claims must visibly say the demo is not validated when WFA media or
  walkthrough copy is present.
- Media must use deterministic reviewed demo fixtures derived from public
  fixtures or public examples and pass redaction checks.

## Quant Honesty

README must not claim:

- profitability
- market edge
- live-trading readiness
- broker execution correctness
- real-data correctness
- survivorship-free or point-in-time universe coverage
- WFA validity from synthetic demo media

## Verification Evidence

Required before PASS:

- focused README / capture / AI skill tests pass
- capture manifest check passes
- bilingual capture/promote passes
- frontend build passes when frontend code changed
- mojibake scan clean
- local link scan clean
- README media existence and dimensions checked
- independent spec, code-quality, and quant gates pass when public wording,
  quant interpretation, or release-boundary behavior changes

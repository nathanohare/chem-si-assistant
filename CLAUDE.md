# SI Assistant — notes for Claude Code

**Read `SPEC.md` first.** It is the source of truth for what to build. `Overview of Functions.md` is the original idea document; `Example SIs/` and `Example Author Guidelines/` are reference material for output format and journal requirements.

## About the project owner
- Nathan is a synthetic organic chemist, not a professional programmer (some Python experience). Explain decisions in plain language, avoid jargon, and say what he needs to do (install, run, test) step by step.
- He knows the chemistry well — trust his domain judgement and ask when chemistry conventions are ambiguous.

## How to work
- Build in the phases listed in SPEC.md §12. Finish, test and demo one phase before starting the next. At the end of each phase, tell Nathan how to run the program and what to try.
- If something in SPEC.md looks wrong or impractical, raise it before building around it; record agreed changes back in SPEC.md.
- Keep all user data in the Project Folder as human-readable files (SPEC.md §2, §4). Never store project data elsewhere.
- Error checks are warnings only — never block the user.
- **Every automatic value must be manually overridable** (SPEC §2.7). Automate the formulaic parts, but always give the user a way to change or replace anything the program fills in, so unusual cases can be handled by hand. Check this for every new feature.
- Must run on Windows, macOS and Linux; Nathan develops on Windows.
- Write unit tests for every calculation (masses, ID assignment, error checks) and keep them passing.
- Use only free, open-source dependencies, and note each one's licence.

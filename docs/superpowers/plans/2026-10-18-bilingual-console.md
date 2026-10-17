# Bilingual administration console

User requested Chinese and English while continuing the implementation plan. This
is a focused Task 17 follow-up; it does not complete Task 18 or release gates.

- [x] Add request-scoped `en` / `zh-CN` selection, a visible persistent switch,
  `Content-Language`, private responses and safe local return paths. Preserve
  current permissions, CSRF, API payloads and source evidence.
- [x] Translate every console template's interface copy, accessible labels, known
  states and upload feedback. Preserve user names, titles, evidence and history
  verbatim. Unknown warning codes and English diagnostic details stay identifiable.
- [x] Verify both languages, concurrent request isolation, escaped user content,
  invalid language/return paths, unchanged owner/reader boundaries and full unit
  regression. Use synthetic providers, one storage fixture at a time; no model
  downloads or calls. Recheck the Docker 1.5 GB aggregate hard cap.
- [ ] Record verification, commit this stage, rebuild distribution, and show a fresh
  frozen bilingual preview. Continue the remaining operational/release work.

Design: Jinja translates explicit interface literals and a finite state catalogue;
never translate rendered HTML or evidence. A request-local ASGI context selects
from a validated preference cookie, then bounded Accept-Language, then English.
The language endpoint only changes an unprivileged display preference. Its return
path must remain under `/admin`; it cannot grant access or mutate KG state.
